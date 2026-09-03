package com.skeleton.home

import com.skeleton.home.homeedge.GmailOAuthHomeEdgeApi
import com.sun.net.httpserver.HttpExchange
import com.sun.net.httpserver.HttpServer
import java.net.InetSocketAddress
import java.util.concurrent.Executors
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class GmailOAuthHomeEdgeApiTest {
    private var server: HttpServer? = null

    @After
    fun tearDown() {
        server?.stop(0)
        server = null
    }

    @Test
    fun startReturnsGoogleAuthorizationUrlWithoutCredentialsInClient() {
        val baseUrl = startServer { exchange ->
            assertEquals("/api/mobile/gmail/oauth/start", exchange.requestURI.path)
            assertEquals("POST", exchange.requestMethod)
            respond(exchange, 200, """{"authorization_url":"https://accounts.google.com/o/oauth2/v2/auth?state=test","message":"Відкрийте Google"}""")
        }

        val result = GmailOAuthHomeEdgeApi(baseUrl).start()

        assertTrue(result.authorizationUrl.startsWith("https://accounts.google.com/"))
        assertEquals("Відкрийте Google", result.message)
    }

    @Test
    fun completePostsReturnedUrlToExistingHomeEdgeEndpoint() {
        val returnedUrl = "http://localhost/?code=fake-code&state=fake-state"
        val baseUrl = startServer { exchange ->
            assertEquals("/api/mobile/gmail/oauth/callback-complete", exchange.requestURI.path)
            assertEquals("POST", exchange.requestMethod)
            val requestBody = exchange.requestBody.bufferedReader().use { it.readText() }
            assertTrue(requestBody.contains("returned_url"))
            assertTrue(requestBody.contains("fake-code"))
            respond(exchange, 200, """{"ok":true,"message":"Gmail авторизовано"}""")
        }

        val result = GmailOAuthHomeEdgeApi(baseUrl).complete(returnedUrl)

        assertEquals("Gmail авторизовано", result.message)
    }

    @Test
    fun completeFailsClosedOnRejectedAuthorization() {
        val baseUrl = startServer { exchange ->
            respond(exchange, 400, """{"message":"OAuth state mismatch"}""")
        }

        val failure = runCatching {
            GmailOAuthHomeEdgeApi(baseUrl).complete("http://localhost/?code=fake&state=wrong")
        }.exceptionOrNull()

        assertTrue(failure?.message?.contains("OAuth state mismatch") == true)
    }

    @Test
    fun completeRejectsBlankReturnedUrlWithoutNetworkCall() {
        val failure = runCatching {
            GmailOAuthHomeEdgeApi("http://127.0.0.1:9").complete("   ")
        }.exceptionOrNull()

        assertTrue(failure?.message?.contains("Вставте адресу після Google") == true)
    }

    private fun startServer(handler: (HttpExchange) -> Unit): String {
        val httpServer = HttpServer.create(InetSocketAddress("127.0.0.1", 0), 0)
        httpServer.executor = Executors.newSingleThreadExecutor()
        httpServer.createContext("/") { exchange -> handler(exchange) }
        httpServer.start()
        server = httpServer
        return "http://127.0.0.1:${httpServer.address.port}"
    }

    private fun respond(exchange: HttpExchange, status: Int, body: String) {
        val bytes = body.toByteArray(Charsets.UTF_8)
        exchange.responseHeaders.add("Content-Type", "application/json; charset=utf-8")
        exchange.sendResponseHeaders(status, bytes.size.toLong())
        exchange.responseBody.use { it.write(bytes) }
    }
}
