package dev.winnie.app

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.material3.Surface
import androidx.compose.ui.Modifier
import dev.winnie.app.push.PushController
import dev.winnie.app.ui.WinnieRoot
import dev.winnie.app.ui.theme.WinnieTheme

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        val container = (application as WinnieApplication).container
        // Opening the app is a user-visible action, so this is an allowed place
        // to restart the background connection if the owner had it on.
        PushController.ensureStarted(this)
        setContent {
            WinnieTheme {
                Surface(modifier = Modifier.fillMaxSize()) {
                    WinnieRoot(container)
                }
            }
        }
    }
}
