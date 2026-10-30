package dev.winnie.app.ui

import androidx.activity.compose.BackHandler
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.viewmodel.compose.viewModel
import androidx.lifecycle.viewmodel.initializer
import androidx.lifecycle.viewmodel.viewModelFactory
import dev.winnie.app.AppContainer
import dev.winnie.app.data.Session
import dev.winnie.app.data.SessionState
import dev.winnie.app.ui.home.HomeScreen
import dev.winnie.app.ui.login.LoginScreen
import dev.winnie.app.ui.login.SessionViewModel
import dev.winnie.app.ui.meeting.MeetingScreen
import dev.winnie.app.ui.record.RecordScreen

@Composable
fun WinnieRoot(container: AppContainer) {
    val sessionViewModel: SessionViewModel = viewModel(
        factory = viewModelFactory {
            initializer {
                SessionViewModel(container.sessions, container.settings)
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
                onSubmit = sessionViewModel::submit,
            )
        }

        is SessionState.LoggedIn -> SignedInFlow(
            container = container,
            session = state.session,
            onSignOut = sessionViewModel::signOut,
        )
    }
}

@Composable
private fun SignedInFlow(
    container: AppContainer,
    session: Session,
    onSignOut: () -> Unit,
) {
    var openMeetingId by rememberSaveable { mutableStateOf<String?>(null) }
    var showRecorder by rememberSaveable { mutableStateOf(false) }

    BackHandler(enabled = showRecorder || openMeetingId != null) {
        when {
            showRecorder -> showRecorder = false
            else -> openMeetingId = null
        }
    }

    val meetingId = openMeetingId
    when {
        showRecorder -> RecordScreen(
            container = container,
            onClose = { showRecorder = false },
            onFinished = { createdId ->
                showRecorder = false
                openMeetingId = createdId
            },
        )

        meetingId != null -> MeetingScreen(
            container = container,
            meetingId = meetingId,
            onBack = { openMeetingId = null },
        )

        else -> HomeScreen(
            container = container,
            user = session.user,
            onOpenMeeting = { openMeetingId = it },
            onRecord = { showRecorder = true },
            onSignOut = onSignOut,
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
