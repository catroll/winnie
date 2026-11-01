package dev.winnie.app.data

import android.content.Context
import androidx.test.core.app.ApplicationProvider
import com.sun.net.httpserver.HttpExchange
import com.sun.net.httpserver.HttpServer
import java.net.InetSocketAddress
import java.util.concurrent.Executors
import kotlinx.coroutines.runBlocking
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner

/**
 * The remember-my-password rule: a successful sign-in saves the pair, an
 * explicit opt-out clears it, and a failed attempt never overwrites the pair
 * that used to work. The Keystore is replaced by a fake box; the HTTP layer is
 * the app's real client against an in-process server.
 */
@RunWith(RobolectricTestRunner::class)
class SessionManagerTest {

    private class FakeBox : SecretBox {
        override fun seal(plain: String): String = "sealed:" + plain

        override fun open(payload: String): String? =
            payload.removePrefix("sealed:").takeIf { payload.startsWith("sealed:") }
    }

    private lateinit var server: HttpServer
    private lateinit var context: Context
    private lateinit var tokenStore: TokenStore
    private lateinit var settings: AppSettings
    private lateinit var credentials: CredentialStore
    private lateinit var sessions: SessionManager
    private var loginStatus = 200

    @Before
    fun setUp() {
        server = HttpServer.create(InetSocketAddress("127.0.0.1", 0), 0)
        server.createContext("/api/v1/auth/login") { exchange -> handleLogin(exchange) }
        server.executor = Executors.newCachedThreadPool()
        server.start()

        context = ApplicationProvider.getApplicationContext()
        listOf("winnie_secure", "winnie_credentials", "winnie_settings").forEach { name ->
            context.getSharedPreferences(name, Context.MODE_PRIVATE).edit().clear().commit()
        }
        val box = FakeBox()
        tokenStore = TokenStore(context, box)
        settings = AppSettings(context)
        credentials = CredentialStore(context, box)
        sessions = SessionManager(tokenStore, settings, credentials)
    }

    @After
    fun tearDown() {
        server.stop(0)
    }

    @Test
    fun aSuccessfulLoginRemembersTheUsernameAndPassword() = runBlocking {
        val result = sessions.login(baseUrl(), "alice", "correct horse")

        assertTrue(result.isSuccess)
        assertEquals(
            SavedCredentials("alice", "correct horse"),
            credentials.saved(),
        )
        assertEquals("token-123", tokenStore.read())
        assertEquals(baseUrl(), settings.serverUrl)
    }

    @Test
    fun optingOutClearsPreviouslyRememberedCredentials() = runBlocking {
        sessions.login(baseUrl(), "alice", "correct horse")
        // The next sign-in deliberately unticks the box.
        sessions.login(baseUrl(), "alice", "correct horse", remember = false)

        assertNull(credentials.saved())
        // The server choice still applies; only the pair is forgotten.
        assertTrue(credentials.hasChoice)
    }

    @Test
    fun aFailedLoginKeepsThePairThatUsedToWork() = runBlocking {
        credentials.save("alice", "correct horse")
        loginStatus = 401

        val result = sessions.login(baseUrl(), "alice", "wrong")

        assertTrue(result.isFailure)
        assertEquals(
            SavedCredentials("alice", "correct horse"),
            credentials.saved(),
        )
        assertNull(tokenStore.read())
    }

    private fun baseUrl(): String = "http://127.0.0.1:${server.address.port}"

    private fun handleLogin(exchange: HttpExchange) {
        exchange.requestBody.readBytes()
        val body = if (loginStatus == 200) {
            """
            {
              "access_token": "token-123",
              "token_type": "bearer",
              "expires_at": "2026-11-30T00:00:00Z",
              "user": {
                "id": "u1",
                "username": "alice",
                "display_name": null,
                "created_at": "2026-10-31T00:00:00"
              }
            }
            """.trimIndent()
        } else {
            """{"detail":"invalid username or password"}"""
        }
        val bytes = body.toByteArray()
        exchange.responseHeaders.add("Content-Type", "application/json")
        exchange.sendResponseHeaders(loginStatus, bytes.size.toLong())
        exchange.responseBody.use { it.write(bytes) }
    }
}
