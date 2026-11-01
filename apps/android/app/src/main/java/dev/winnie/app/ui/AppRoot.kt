package dev.winnie.app.ui

import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.viewmodel.compose.viewModel
import androidx.lifecycle.viewmodel.initializer
import androidx.lifecycle.viewmodel.viewModelFactory
import dev.winnie.app.AppContainer
import dev.winnie.app.data.SessionState
import dev.winnie.app.push.PushController
import dev.winnie.app.ui.login.LoginScreen
import dev.winnie.app.ui.login.SessionViewModel

@Composable
fun WinnieRoot(container: AppContainer) {
    val appContext = LocalContext.current.applicationContext
    val sessionViewModel: SessionViewModel = viewModel(
        factory = viewModelFactory {
            initializer {
                SessionViewModel(container.sessions, container.settings, container.credentials)
            }
        },
    )
    val sessionState by sessionViewModel.sessionState.collectAsStateWithLifecycle()
    val loginUi by sessionViewModel.ui.collectAsStateWithLifecycle()

    when (val state = sessionState) {
        SessionState.Restoring -> LoadingScreen()

        is SessionState.LoggedOut -> {
            LaunchedEffect(state.serverUrl) {
                sessionViewModel.applyServerUrl(state.serverUrl)
            }
            LoginScreen(
                ui = loginUi,
                notice = state.message,
                onServerUrlChange = sessionViewModel::setServerUrl,
                onRegisterModeChange = sessionViewModel::setRegisterMode,
                onUsernameChange = sessionViewModel::setUsername,
                onPasswordChange = sessionViewModel::setPassword,
                onRememberChange = sessionViewModel::setRemember,
                onSubmit = sessionViewModel::submit,
            )
        }

        is SessionState.LoggedIn -> SignedInFlow(
            container = container,
            session = state.session,
            onSignOut = {
                PushController.stop(appContext)
                sessionViewModel.signOut()
            },
        )
    }
}

@Composable
fun LoadingScreen() {
    Box(
        modifier = Modifier.fillMaxSize(),
        contentAlignment = Alignment.Center,
    ) {
        CircularProgressIndicator()
    }
}
