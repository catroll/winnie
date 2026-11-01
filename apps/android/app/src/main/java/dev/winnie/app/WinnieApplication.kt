package dev.winnie.app

import android.app.Application
import dev.winnie.app.phone.PhoneSyncLoop
import dev.winnie.app.phone.PhoneSyncScheduler

class WinnieApplication : Application() {

    lateinit var container: AppContainer
        private set

    override fun onCreate() {
        super.onCreate()
        AppStrings.init(this)
        container = AppContainer(this)
        // The 5-second loop runs with the process: while the app is open and
        // for as long as the alert service keeps it alive. Content observers
        // wake it; the WorkManager worker is the fallback for a killed process.
        PhoneSyncScheduler.registerObservers(this)
        PhoneSyncLoop.start(this)
        if (container.phonePrefs.masterEnabled) {
            PhoneSyncScheduler.configure(this, container.phonePrefs.intervalMinutes)
        }
    }
}
