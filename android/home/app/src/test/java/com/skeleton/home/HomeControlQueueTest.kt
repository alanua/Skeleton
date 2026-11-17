package com.skeleton.home

import kotlinx.coroutines.*
import org.junit.Assert.*
import org.junit.Test

class HomeControlQueueTest{
    @Test fun orderedTapsNeverExecuteConcurrently()=runBlocking{
        val queue=HomeControlQueue();val received=mutableListOf<String>();var concurrent=0;var peak=0
        val worker=launch{queue.consume({concurrent++;peak=maxOf(peak,concurrent);delay(5);received.add(it.action);concurrent--},{throw it})}
        listOf("up","right","ok","back").forEach{assertTrue(queue.submit(HomeControlCommand("remote",it)))}
        withTimeout(1_000){while(received.size<4)delay(5)}
        queue.close();worker.join()
        assertEquals(listOf("up","right","ok","back"),received);assertEquals(1,peak)
    }
    @Test fun failedCommandDoesNotHideErrorOrBlockFollowingCommand()=runBlocking{
        val queue=HomeControlQueue();val received=mutableListOf<String>();val errors=mutableListOf<String>()
        val worker=launch{queue.consume({if(it.action=="bad")error("failed");received.add(it.action)},{errors.add(it.message.orEmpty())})}
        queue.submit(HomeControlCommand("remote","bad"));queue.submit(HomeControlCommand("remote","ok"))
        withTimeout(1_000){while(received.isEmpty())delay(5)}
        queue.close();worker.join();assertEquals(listOf("failed"),errors);assertEquals(listOf("ok"),received)
    }
    @Test fun fullOrClosedQueueRejectsExplicitly(){
        val queue=HomeControlQueue(1)
        assertTrue(queue.submit(HomeControlCommand("remote","up")))
        assertFalse(queue.submit(HomeControlCommand("remote","down")))
        queue.close();assertFalse(queue.submit(HomeControlCommand("remote","ok")))
    }
    @Test fun contextDisposalDropsOldPendingCommands()=runBlocking{
        val old=HomeControlQueue();old.submit(HomeControlCommand("remote","old"));old.close()
        val received=mutableListOf<String>();val fresh=HomeControlQueue()
        val worker=launch{fresh.consume({received.add(it.action)},{throw it})}
        fresh.submit(HomeControlCommand("remote","new"));withTimeout(1_000){while(received.isEmpty())delay(5)}
        fresh.close();worker.join();assertEquals(listOf("new"),received)
    }
}
