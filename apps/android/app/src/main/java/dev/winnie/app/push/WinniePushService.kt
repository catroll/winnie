package dev.winnie.app.push

import android.app.Service
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.Build
import android.os.IBinder
import androidx.core.app.ServiceCompat
import dev.winnie.app.AppContainer
import dev.winnie.app.WinnieApplication
import dev.winnie.app.data.PushEvent
import dev.winnie.app.data.WinnieApi
import dev.winnie.app.phone.PhoneSyncScheduler
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.serialization.decodeFromString
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener

/**
 * Keeps one WebSocket to `/api/v1/push/ws` open for as long as the owner has
 * enabled it.
 *
 * Android does not let a normal process hold a socket overnight, so this is a
 * foreground service with a persistent low-importance notification. That is the
 * cost of a self-hosted push channel: no vendor service, no Google dependency,
 * but a visible notification and some battery use. The service reconnects with
 * exponential backoff, survives a task removal (`START_STICKY`), restarts on
 * boot when enabled, and hands `sync` control events to the phone sync worker
 * so a confirmed write does not wait for the periodic schedule.
 */
class WinniePushService : Service() {

    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private lateinit var container: AppContainer
    private lateinit var prefs: PushPrefs
    private var socket: WebSocket? = null
    private var reconnectJob: Job? = null
    private var connecting = false
    private var attempt = 0

    @Volatile
    private var closing = false

    override fun onCreate() {
        super.onCreate()
        container = (application as WinnieApplication).container
        prefs = container.pushPrefs
        PushNotifications.createChannels(this)
        startForegroundCompat()
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (intent?.action == ACTION_STOP) {
            PushController.stop(this)
            stopSelf()
            return START_NOT_STICKY
        }
        if (!prefs.enabled) {
            stopSelf()
            return START_NOT_STICKY
        }
        connect()
        return START_STICKY
    }

    override fun onDestroy() {
        closing = true
        reconnectJob?.cancel()
        socket?.close(1000, "service stopped")
        socket = null
        scope.cancel()
        updateStatus("stopped")
        super.onDestroy()
    }

    override fun onBind(intent: Intent?): IBinder? = null

    private fun startForegroundCompat() {
        val notification = PushNotifications.foreground(this)
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.UPSIDE_DOWN_CAKE) {
            ServiceCompat.startForeground(
                this,
                PushNotifications.FOREGROUND_ID,
                notification,
                ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE,
            )
        } else {
            @Suppress("DEPRECATION")
            startForeground(PushNotifications.FOREGROUND_ID, notification)
        }
    }

    private fun connect() {
        if (closing || !prefs.enabled || socket != null || connecting) return
        connecting = true
        updateStatus("connecting")
        scope.launch {
            val api = container.phoneApi()
            if (api == null) {
                connecting = false
                updateStatus("signed out", "Sign in to receive reminders.")
                scheduleReconnect()
                return@launch
            }
            // A device id lets the server target control events at this phone.
            // Without one, only broadcast notifications arrive.
            val deviceId = runCatching {
                container.newPhoneSyncEngine().ensureRegisteredDevice()?.id
            }.getOrNull() ?: container.phonePrefs.deviceId
            if (closing || !prefs.enabled) {
                connecting = false
                return@launch
            }
            // A fresh install starts near the present instead of replaying the
            // whole retention window as notifications.
            if (prefs.lastSeq == 0L) {
                val status = runCatching { api.pushStatus() }.getOrNull()
                if (status != null && status.lastSeq > 0) {
                    prefs.lastSeq = (status.lastSeq - FRESH_INSTALL_BACKLOG)
                        .coerceAtLeast(0L)
                }
            }
            socket = api.openPushStream(deviceId, prefs.lastSeq, listener)
        }
    }

    private val listener = object : WebSocketListener() {
        override fun onOpen(webSocket: WebSocket, response: Response) {
            connecting = false
            attempt = 0
            updateStatus("connected")
        }

        override fun onMessage(webSocket: WebSocket, text: String) {
            handleFrame(text)
        }

        override fun onFailure(
            webSocket: WebSocket,
            t: Throwable,
            response: Response?,
        ) {
            connecting = false
            socket = null
            val error = when (response?.code) {
                401 -> "The session expired. Sign in again."
                403 -> "This phone is revoked on the server."
                else -> t.message?.takeIf { it.isNotBlank() } ?: "Connection failed."
            }
            updateStatus("disconnected", error)
            scheduleReconnect()
        }

        override fun onClosed(webSocket: WebSocket, code: Int, reason: String) {
            connecting = false
            socket = null
            if (closing) return
            updateStatus("disconnected", reason.ifBlank { "Connection closed." })
            scheduleReconnect()
        }
    }

    private fun handleFrame(text: String) {
        val event = runCatching {
            WinnieApi.json.decodeFromString<PushEvent>(text)
        }.getOrNull() ?: return
        when (event.type) {
            "event" -> {
                if (event.seq <= prefs.lastSeq) return
                prefs.lastSeq = event.seq
                val at = System.currentTimeMillis()
                prefs.lastEventAt = at
                PushStatusBus.update { it.copy(lastEventAt = at) }
                route(event)
            }
            "ready" -> updateStatus("connected")
            "ping" -> Unit
        }
    }

    private fun route(event: PushEvent) {
        when (val action = pushActionFor(event)) {
            // A confirmed phone command: run the sync now instead of waiting
            // for the periodic worker.
            PushAction.RunSync -> PhoneSyncScheduler.requestSync(this, delaySeconds = 0)
            is PushAction.ShowNotification -> PushNotifications.show(this, action.event)
            PushAction.Ignore -> Unit
        }
    }

    private fun scheduleReconnect() {
        if (closing || !prefs.enabled) return
        reconnectJob?.cancel()
        val wait = minOf(RECONNECT_MAX_MS, RECONNECT_BASE_MS shl minOf(attempt, 5))
        attempt++
        reconnectJob = scope.launch {
            delay(wait)
            connect()
        }
    }

    private fun updateStatus(state: String, error: String = "") {
        prefs.status = state
        val message = if (state == "connected") "" else error.ifBlank { prefs.lastError }
        prefs.lastError = message
        PushStatusBus.update {
            it.copy(state = state, error = message, lastEventAt = prefs.lastEventAt)
        }
    }

    companion object {
        const val ACTION_STOP = "dev.winnie.app.push.STOP"
        private const val RECONNECT_BASE_MS = 2_000L
        private const val RECONNECT_MAX_MS = 60_000L
        private const val FRESH_INSTALL_BACKLOG = 50L
    }
}
