package dev.winnie.app.audio

import android.Manifest
import android.app.Service
import android.content.Intent
import android.content.pm.PackageManager
import android.content.pm.ServiceInfo
import android.os.Build
import android.os.Handler
import android.os.IBinder
import android.os.Looper
import android.util.Log
import androidx.core.app.ServiceCompat
import androidx.core.content.ContextCompat
import dev.winnie.app.MainActivity
import java.util.concurrent.ExecutorService
import java.util.concurrent.Executors

/**
 * Holds the microphone and listens for the wake word.
 *
 * **Why a foreground service.** Android does not let an ordinary process keep
 * the microphone open, and it does not let a third-party app use the low-power
 * hotword path that Google Assistant uses. A `microphone` foreground service
 * with a visible notification is the only supported way to listen while the
 * screen is off, and the notification is deliberate: the owner can always see
 * that the microphone is in use.
 *
 * **What Android allows.** The service must be started while the app is
 * visible (Android 14 rejects a while-in-use foreground service started from
 * the background), and Android 15 forbids starting this type from
 * `BOOT_COMPLETED`. Listening therefore survives screen-off, task removal and
 * process death, but after a reboot the owner opens the app once before it
 * resumes. That limit is the platform's, not the app's, which is why the switch
 * is explicit.
 *
 * **One thread owns the microphone.** Starting, stopping and restarting the
 * detector all run on one worker, because releasing the device takes as long as
 * one 100 ms read and a restart that overtook a stop would leave two
 * `AudioRecord`s open. Only the notification and the activity launch run on the
 * main thread.
 */
class WakeWordService : Service() {

    private lateinit var prefs: WakePrefs
    private var detector: WakeWordDetector? = null
    private var ring: PcmRingBuffer? = null

    private val handler = Handler(Looper.getMainLooper())
    private val worker: ExecutorService = Executors.newSingleThreadExecutor()

    /** The microphone came back after a wake nobody used. */
    private val resumeListening = Runnable {
        worker.execute { startDetector() }
    }

    override fun onCreate() {
        super.onCreate()
        prefs = WakePrefs(this)
        WakeNotifications.createChannels(this)
        startForegroundCompat()
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (intent?.action == ACTION_STOP) {
            prefs.enabled = false
            stopSelf()
            return START_NOT_STICKY
        }
        if (!prefs.enabled) {
            stopSelf()
            return START_NOT_STICKY
        }
        if (intent?.action == ACTION_PAUSE) {
            // The app is speaking; its own voice must not be heard as a wake
            // word. The service stays alive so one resume brings listening back.
            handler.removeCallbacks(resumeListening)
            worker.execute {
                detector?.stop()
                detector = null
            }
            return START_STICKY
        }
        if (intent?.action == ACTION_RESUME) {
            handler.removeCallbacks(resumeListening)
            worker.execute { startDetector() }
            return START_STICKY
        }
        worker.execute { startDetector() }
        return START_STICKY
    }

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onDestroy() {
        handler.removeCallbacks(resumeListening)
        worker.execute {
            detector?.stop()
            detector = null
            ring = null
        }
        worker.shutdown()
        super.onDestroy()
    }

    private fun startForegroundCompat() {
        val notification = WakeNotifications.service(this, prefs.keyword)
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.UPSIDE_DOWN_CAKE) {
            ServiceCompat.startForeground(
                this,
                WakeNotifications.SERVICE_ID,
                notification,
                ServiceInfo.FOREGROUND_SERVICE_TYPE_MICROPHONE,
            )
        } else {
            @Suppress("DEPRECATION")
            startForeground(WakeNotifications.SERVICE_ID, notification)
        }
    }

    private fun startDetector() {
        if (!prefs.enabled) return
        if (detector != null) return
        if (ContextCompat.checkSelfPermission(this, Manifest.permission.RECORD_AUDIO) !=
            PackageManager.PERMISSION_GRANTED
        ) {
            // Without the permission there is nothing to listen with; make that
            // visible instead of leaving a permanent notification that lies.
            prefs.enabled = false
            stopSelf()
            return
        }
        val ring = PcmRingBuffer(PRE_ROLL_BYTES).also { this.ring = it }
        val detector = SherpaWakeWordDetector(this, ring)
        this.detector = detector
        detector.start(
            onWake = { keyword -> handler.post { onWake(keyword) } },
            onError = { error ->
                handler.post {
                    Log.e(TAG, "wake word engine failed", error)
                    prefs.enabled = false
                    stopSelf()
                }
            },
        )
    }

    /** Releases the microphone first, then asks the app to take the turn. */
    private fun onWake(keyword: String) {
        val preRoll = ring?.snapshot() ?: ByteArray(0)
        worker.execute {
            detector?.stop()
            detector = null
            handler.post { announce(keyword, preRoll) }
        }
    }

    private fun announce(keyword: String, preRoll: ByteArray) {
        // The keyword file cannot hold spaces in its `@` label, so the display
        // form restores them for the notification.
        val spoken = keyword.replace('_', ' ')
        Log.i(TAG, "wake word detected: $spoken (${preRoll.size} pre-roll bytes)")
        val manager = getSystemService(android.app.NotificationManager::class.java)
        manager?.notify(
            WakeNotifications.ALERT_ID,
            WakeNotifications.detected(this, spoken, preRoll),
        )
        // A background activity start is refused on modern Android unless the
        // app is visible or allowed to draw over other apps. Trying is still
        // worth it: when the screen is on and Winnie is in front, this opens the
        // turn without a tap. When it is refused, the notification above is the
        // path in, so a lost direct start cannot lose the wake.
        runCatching {
            val intent = Intent(this, MainActivity::class.java)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_SINGLE_TOP)
                .putExtra(MainActivity.EXTRA_VOICE_TURN, true)
                .putExtra(MainActivity.EXTRA_VOICE_PREROLL, preRoll)
            startActivity(intent)
        }.onFailure { Log.w(TAG, "could not open the voice turn directly", it) }

        handler.postDelayed(resumeListening, WAKE_RESUME_DELAY_MS)
    }

    companion object {
        const val ACTION_STOP = "dev.winnie.app.action.WAKE_STOP"
        const val ACTION_RESUME = "dev.winnie.app.action.WAKE_RESUME"
        const val ACTION_PAUSE = "dev.winnie.app.action.WAKE_PAUSE"

        /**
         * Two seconds of 16 kHz mono. Long enough for the detector's 320 ms
         * decision plus the activity launch, short enough that the turn does not
         * start with an unrelated sentence.
         */
        private const val PRE_ROLL_BYTES = 16_000 * 2 * 2

        /** How long the microphone stays with the app after a detection. */
        private const val WAKE_RESUME_DELAY_MS = 60_000L

        private const val TAG = "WinnieWake"
    }
}
