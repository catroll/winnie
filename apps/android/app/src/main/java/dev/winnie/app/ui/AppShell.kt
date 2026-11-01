package dev.winnie.app.ui

import androidx.activity.compose.BackHandler
import androidx.annotation.DrawableRes
import androidx.annotation.StringRes
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.isImeVisible
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.NavigationBar
import androidx.compose.material3.NavigationBarItem
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.res.stringResource
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.viewmodel.compose.viewModel
import androidx.lifecycle.viewmodel.initializer
import androidx.lifecycle.viewmodel.viewModelFactory
import dev.winnie.app.AppContainer
import dev.winnie.app.R
import dev.winnie.app.data.Session
import dev.winnie.app.ui.account.AccountScreen
import dev.winnie.app.ui.account.AccountViewModel
import dev.winnie.app.ui.chat.ChatHistoryScreen
import dev.winnie.app.ui.chat.ChatScreen
import dev.winnie.app.ui.chat.ChatViewModel
import dev.winnie.app.ui.home.HomeScreen
import dev.winnie.app.ui.meeting.MeetingScreen
import dev.winnie.app.ui.phone.PhoneDataScreen
import dev.winnie.app.ui.push.PushSettingsScreen
import dev.winnie.app.ui.record.RecordScreen

/**
 * The four top-level destinations. Everything else is a detail screen opened on
 * top of them. Four text actions used to share the app bar with the title, which
 * is what squeezed "Winnie" onto two lines; each destination now owns a tab.
 */
enum class SignedInTab(@StringRes val labelRes: Int, @DrawableRes val icon: Int) {
    Chat(R.string.tab_chat, R.drawable.ic_chat),
    Meetings(R.string.tab_meetings, R.drawable.ic_meetings),
    Phone(R.string.tab_phone, R.drawable.ic_phone_data),
    Account(R.string.tab_account, R.drawable.ic_account),
}

@OptIn(ExperimentalLayoutApi::class)
@Composable
fun SignedInFlow(
    container: AppContainer,
    session: Session,
    onSignOut: () -> Unit,
) {
    var tab by rememberSaveable { mutableStateOf(SignedInTab.Chat) }
    var openMeetingId by rememberSaveable { mutableStateOf<String?>(null) }
    var showRecorder by rememberSaveable { mutableStateOf(false) }
    var showAlerts by rememberSaveable { mutableStateOf(false) }
    var showHistory by rememberSaveable { mutableStateOf(false) }

    // A conversation must not be shared with another account on the same
    // phone, so the view models are keyed by the signed-in user.
    val chatViewModel: ChatViewModel = viewModel(
        key = "chat:" + session.user.id,
        factory = viewModelFactory {
            initializer { ChatViewModel(container.sessions, container.settings) }
        },
    )
    val chatUi by chatViewModel.ui.collectAsStateWithLifecycle()

    val accountViewModel: AccountViewModel = viewModel(
        key = "account:" + session.user.id,
        factory = viewModelFactory {
            initializer { AccountViewModel(container.credentials) }
        },
    )
    val accountUi by accountViewModel.ui.collectAsStateWithLifecycle()

    // Entering the account tab is where the saved-password state is shown; read
    // it again instead of trusting what it was when the shell first composed.
    LaunchedEffect(tab) {
        if (tab == SignedInTab.Account) accountViewModel.refresh()
    }

    BackHandler(
        enabled = showAlerts || showHistory || showRecorder ||
            openMeetingId != null || tab != SignedInTab.Chat,
    ) {
        when {
            showAlerts -> showAlerts = false
            showHistory -> showHistory = false
            showRecorder -> showRecorder = false
            openMeetingId != null -> openMeetingId = null
            else -> tab = SignedInTab.Chat
        }
    }

    when {
        showAlerts -> PushSettingsScreen(
            container = container,
            onBack = { showAlerts = false },
        )

        showRecorder -> RecordScreen(
            container = container,
            onClose = { showRecorder = false },
            onFinished = { createdId ->
                showRecorder = false
                openMeetingId = createdId
            },
        )

        openMeetingId != null -> MeetingScreen(
            container = container,
            meetingId = openMeetingId.orEmpty(),
            onBack = { openMeetingId = null },
        )

        showHistory -> ChatHistoryScreen(
            ui = chatUi,
            onOpen = { conversationId ->
                showHistory = false
                tab = SignedInTab.Chat
                chatViewModel.openConversation(conversationId)
            },
            onNewChat = {
                showHistory = false
                tab = SignedInTab.Chat
                chatViewModel.newChat()
            },
            onRefresh = chatViewModel::loadHistory,
            onBack = { showHistory = false },
        )

        else -> {
            val navBar: @Composable () -> Unit = {
                WinnieNavBar(current = tab, onSelect = { tab = it })
            }
            when (tab) {
                SignedInTab.Chat -> ChatScreen(
                    ui = chatUi,
                    onInputChange = chatViewModel::onInputChange,
                    onSend = chatViewModel::send,
                    onNewChat = chatViewModel::newChat,
                    onOpenHistory = {
                        showHistory = true
                        chatViewModel.loadHistory()
                    },
                    onDismissError = chatViewModel::dismissError,
                    // With the keyboard open the navigation bar would sit
                    // between the composer and the cursor; hide it while typing.
                    bottomBar = { if (!WindowInsets.isImeVisible) navBar() },
                )

                SignedInTab.Meetings -> HomeScreen(
                    container = container,
                    onOpenMeeting = { openMeetingId = it },
                    onRecord = { showRecorder = true },
                    bottomBar = navBar,
                )

                SignedInTab.Phone -> PhoneDataScreen(
                    container = container,
                    bottomBar = navBar,
                )

                SignedInTab.Account -> AccountScreen(
                    user = session.user,
                    serverUrl = session.serverUrl,
                    savedPassword = accountUi.savedPassword,
                    onForgetPassword = accountViewModel::forgetSavedPassword,
                    onOpenAlerts = { showAlerts = true },
                    onSignOut = onSignOut,
                    bottomBar = navBar,
                )
            }
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
internal fun WinnieNavBar(
    current: SignedInTab,
    onSelect: (SignedInTab) -> Unit,
) {
    NavigationBar {
        SignedInTab.entries.forEach { destination ->
            NavigationBarItem(
                selected = destination == current,
                onClick = { onSelect(destination) },
                icon = {
                    Icon(
                        painter = painterResource(destination.icon),
                        contentDescription = null,
                    )
                },
                label = { Text(stringResource(destination.labelRes)) },
            )
        }
    }
}
