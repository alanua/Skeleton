package com.skeleton.home

import java.io.File
import java.io.IOException
import java.net.InetAddress
import java.net.ServerSocket
import java.net.SocketTimeoutException
import java.security.MessageDigest
import java.util.concurrent.Executors
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.runBlocking
import org.junit.Assert.*
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder

class HomeUpdateDownloadTest {
    @get:Rule val temp = TemporaryFolder()
    private val payload = ByteArray(150_000) { (it % 251).toByte() }
    private fun sha(bytes:ByteArray)=MessageDigest.getInstance("SHA-256").digest(bytes).joinToString(""){"%02x".format(it.toInt() and 255)}
    private fun serve(body:ByteArray=payload,status:Int=200,length:Long=body.size.toLong(),bodyDelayMs:Long=0,action:(String)->Unit){
        val server=ServerSocket(0,8,InetAddress.getByName("127.0.0.1"))
        val executor=Executors.newCachedThreadPool { task -> Thread(task).apply{isDaemon=true} }
        val listener=Thread{
            while(!server.isClosed){
                val socket=try{server.accept()}catch(_:IOException){break}
                executor.execute{
                    try{socket.use{client->
                        client.soTimeout=2_000
                        val input=client.getInputStream().bufferedReader()
                        while(true){val line=input.readLine()?:break;if(line.isEmpty())break}
                        val output=client.getOutputStream()
                        val framing=if(status==200&&length==0L)"Transfer-Encoding: chunked\r\n" else "Content-Length: "+(if(status==200)length else 0)+"\r\n"
                        output.write(("HTTP/1.1 "+status+" Test\r\n"+framing+"Connection: close\r\n\r\n").toByteArray())
                        output.flush()
                        if(bodyDelayMs>0)Thread.sleep(bodyDelayMs)
                        if(status==200){
                            if(length==0L)output.write((body.size.toString(16)+"\r\n").toByteArray())
                            output.write(body)
                            if(length==0L)output.write("\r\n0\r\n\r\n".toByteArray())
                            output.flush()
                        }
                    }}catch(_:Exception){
                        // A downloader may reject headers or cancel a partial response.
                    }
                }
            }
        }.apply{isDaemon=true;start()}
        try{action("http://127.0.0.1:"+server.localPort+"/update")}
        finally{server.close();listener.join(500);executor.shutdownNow()}
    }
    private fun assertEmpty(dir:File)=assertEquals(emptyList<String>(),dir.listFiles().orEmpty().map{it.name})

    @Test fun successReturnsOnlyVerifiedApkAndEndsProgressAt100(){
        val dir=temp.newFolder();val progress=mutableListOf<Int>()
        serve{url->
            val result=runBlocking{downloadVerifiedHomeUpdate(url,dir,sha(payload),payload.size.toLong(),onProgress={progress.add(it)})}
            assertEquals("apk",result.extension);assertArrayEquals(payload,result.readBytes())
            assertEquals(listOf(result),dir.listFiles().orEmpty().toList())
            assertEquals(0,progress.first());assertEquals(100,progress.last())
            assertTrue(progress.dropLast(1).all{it in 0..99})
        }
    }
    @Test fun httpErrorRemovesPartialAndReportsStatus(){
        val dir=temp.newFolder()
        serve(status=503){url->
            val error=assertThrows(IOException::class.java){runBlocking{downloadVerifiedHomeUpdate(url,dir,sha(payload),payload.size.toLong())}}
            assertTrue(error.message.orEmpty().contains("503"));assertEmpty(dir)
        }
    }
    @Test fun hashMismatchNeverReturnsApkOr100(){
        val dir=temp.newFolder();val progress=mutableListOf<Int>()
        serve{url->
            assertThrows(IOException::class.java){runBlocking{downloadVerifiedHomeUpdate(url,dir,"0".repeat(64),payload.size.toLong(),onProgress={progress.add(it)})}}
            assertFalse(progress.contains(100));assertEmpty(dir)
        }
    }
    @Test fun wrongAnnouncedSizeIsRejected(){
        val dir=temp.newFolder()
        serve{url->
            assertThrows(IOException::class.java){runBlocking{downloadVerifiedHomeUpdate(url,dir,sha(payload),payload.size.toLong()+1)}}
            assertEmpty(dir)
        }
    }
    @Test fun truncatedBodyIsRejectedAndCleaned(){
        val dir=temp.newFolder()
        serve(length=payload.size.toLong()+20){url->
            assertThrows(IOException::class.java){runBlocking{downloadVerifiedHomeUpdate(url,dir,sha(payload),payload.size.toLong()+20,readTimeoutMs=250)}}
            assertEmpty(dir)
        }
    }
    @Test fun chunkedResponseCannotExceedManifestSize(){
        val dir=temp.newFolder()
        serve(length=0){url->
            assertThrows(IOException::class.java){runBlocking{downloadVerifiedHomeUpdate(url,dir,sha(payload),100)}}
            assertEmpty(dir)
        }
    }
    @Test fun stalledReadTimesOutInsteadOfWaitingForever(){
        val dir=temp.newFolder();val start=System.nanoTime()
        serve(bodyDelayMs=1_000){url->
            assertThrows(SocketTimeoutException::class.java){runBlocking{downloadVerifiedHomeUpdate(url,dir,sha(payload),payload.size.toLong(),readTimeoutMs=80)}}
            assertEmpty(dir)
        }
        assertTrue("read timeout must be bounded",(System.nanoTime()-start)/1_000_000<2_000)
    }
    @Test fun overallDeadlineAppliesEvenWhenConnectionResponds(){
        val dir=temp.newFolder();var clock=0L
        serve{url->
            assertThrows(SocketTimeoutException::class.java){runBlocking{downloadVerifiedHomeUpdate(url,dir,sha(payload),payload.size.toLong(),maxDurationMs=120,clockMs={clock.also{clock+=30}})}}
            assertEmpty(dir)
        }
    }
    @Test fun cancellationDuringDownloadPropagatesAndRemovesPartial(){
        val dir=temp.newFolder()
        serve{url->
            assertThrows(CancellationException::class.java){runBlocking{downloadVerifiedHomeUpdate(url,dir,sha(payload),payload.size.toLong(),onProgress={if(it>0)throw CancellationException("cancel")})}}
            assertEmpty(dir)
        }
    }
    @Test fun cancellationBeforeReturnAlsoRemovesCompletedFile(){
        val dir=temp.newFolder()
        serve{url->
            assertThrows(CancellationException::class.java){runBlocking{downloadVerifiedHomeUpdate(url,dir,sha(payload),payload.size.toLong(),onProgress={if(it==100)throw CancellationException("cancel")})}}
            assertEmpty(dir)
        }
    }
    @Test fun failedDownloadCanBeRetriedWithoutStaleFiles(){
        val dir=temp.newFolder()
        serve{url->
            assertThrows(IOException::class.java){runBlocking{downloadVerifiedHomeUpdate(url,dir,"0".repeat(64),payload.size.toLong())}}
            val result=runBlocking{downloadVerifiedHomeUpdate(url,dir,sha(payload),payload.size.toLong())}
            assertArrayEquals(payload,result.readBytes());assertEquals(1,dir.listFiles().orEmpty().size)
        }
    }
    @Test fun invalidMetadataAndNonHttpUrlDoNotCreatePartialFiles(){
        val dir=temp.newFolder()
        assertThrows(IllegalArgumentException::class.java){runBlocking{downloadVerifiedHomeUpdate("file:///unused",dir,sha(payload),payload.size.toLong())}}
        assertThrows(IllegalArgumentException::class.java){runBlocking{downloadVerifiedHomeUpdate("http://127.0.0.1/unused",dir,"bad",payload.size.toLong())}}
        assertThrows(IllegalArgumentException::class.java){runBlocking{downloadVerifiedHomeUpdate("http://127.0.0.1/unused",dir,sha(payload),0)}}
        assertEmpty(dir)
    }
}
