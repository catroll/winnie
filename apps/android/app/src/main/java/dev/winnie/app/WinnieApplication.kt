package dev.winnie.app

import android.app.Application
import dev.winnie.app.phone.PhoneSyncScheduler

class WinnieApplication : Application() {

    lateinit var container: AppContainer
        private set

    override fun onCreate() {
        super.onCreate()
        container = AppContainer(this)
        // Content observers only live as long as the process; the periodic
        // worker is the safety net and is (re)configured after every sync.
        PhoneSyncScheduler.registerObservers(this)
        if (container.phonePrefs.masterEnabled) {
            PhoneSyncScheduler.configure(this, container.phonePrefs.intervalMinutes)
        }
    }
}
