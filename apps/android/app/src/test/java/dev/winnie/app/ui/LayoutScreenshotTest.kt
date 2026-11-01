package dev.winnie.app.ui

import android.graphics.Bitmap
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.asAndroidBitmap
import androidx.compose.ui.test.captureToImage
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.onRoot
import androidx.compose.ui.test.performClick
import androidx.test.core.app.ApplicationProvider
import dev.winnie.app.WinnieApplication
import dev.winnie.app.data.AssistantConversationSummary
import dev.winnie.app.data.DEFAULT_SERVER_URL
import dev.winnie.app.data.User
import dev.winnie.app.ui.account.AccountScreen
import dev.winnie.app.ui.chat.ChatHistoryScreen
import dev.winnie.app.ui.chat.ChatMessage
import dev.winnie.app.ui.chat.ChatScreen
import dev.winnie.app.ui.chat.ChatUiState
import dev.winnie.app.ui.login.LoginScreen
import dev.winnie.app.ui.login.LoginUiState
import dev.winnie.app.ui.phone.PhoneDataScreen
import dev.winnie.app.ui.push.PushSettingsScreen
import dev.winnie.app.ui.theme.WinnieTheme
import java.io.File
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config
import org.robolectric.annotation.GraphicsMode

/**
 * Renders the phone layouts at 360×800 dp and writes PNGs under
 * `app/build/screenshots/`.
 *
 * There is no emulator on the build machine, so this is the only way to look at
 * what a screen actually looks like — and in particular to confirm that a title
 * stays on one line instead of trusting the layout code. The test asserts
 * nothing about pixels; it is a rendering aid, and the images are gitignored
 * with the rest of `build/`.
 */
@RunWith(RobolectricTestRunner::class)
@GraphicsMode(GraphicsMode.Mode.NATIVE)
@Config(qualifiers = "w360dp-h800dp-xhdpi")
class LayoutScreenshotTest {

    @get:Rule
    val compose = createComposeRule()

    private fun capture(name: String) {
        compose.waitForIdle()
        val bitmap = compose.onRoot().captureToImage().asAndroidBitmap()
        val dir = File("build/screenshots").apply { mkdirs() }
        File(dir, "$name.png").outputStream().use { stream ->
            bitmap.compress(Bitmap.CompressFormat.PNG, 100, stream)
        }
    }

    @Composable
    private fun navBar(current: SignedInTab) {
        WinnieNavBar(current = current, onSelect = {})
    }

    @Test
    fun chatWithMarkdown() {
        val state = ChatUiState(
            conversationId = "c1",
            messages = listOf(
                ChatMessage(0, role = "user", text = "What is on my schedule today?"),
                ChatMessage(
                    1,
                    role = "tool",
                    name = "schedule",
                    text = "09:30 Standup\n14:00 Review the news draft",
                ),
                ChatMessage(
                    2,
                    role = "assistant",
                    text = """
                        ## Today

                        You have **two** events:

                        | Time  | What |
                        |-------|------|
                        | 09:30 | Standup |
                        | 14:00 | Review the news draft |

                        - `09:30` Standup
                        - `14:00` Review the news draft

                        > The 14:00 review is the only one with a file attached.

                        See [the runbook](https://winnie.example.com/runbook).

                        ```kotlin
                        val reminder = "Review the draft"
                        ```
                    """.trimIndent(),
                    tools = listOf("schedule"),
                ),
            ),
        )
        compose.setContent {
            WinnieTheme {
                ChatScreen(
                    ui = state,
                    onInputChange = {},
                    onSend = {},
                    onNewChat = {},
                    onOpenHistory = {},
                    onDismissError = {},
                    bottomBar = { navBar(SignedInTab.Chat) },
                )
            }
        }
        capture("chat-with-markdown")
    }

    @Test
    fun chatEmptyState() {
        compose.setContent {
            WinnieTheme {
                ChatScreen(
                    ui = ChatUiState(),
                    onInputChange = {},
                    onSend = {},
                    onNewChat = {},
                    onOpenHistory = {},
                    onDismissError = {},
                    bottomBar = { navBar(SignedInTab.Chat) },
                )
            }
        }
        capture("chat-empty")
    }

    @Test
    fun chatWithKeyboardError() {
        val state = ChatUiState(
            messages = listOf(ChatMessage(0, role = "user", text = "Hello")),
            error = "Could not connect to the server. Check the address and that Winnie is running.",
        )
        compose.setContent {
            WinnieTheme {
                ChatScreen(
                    ui = state,
                    onInputChange = {},
                    onSend = {},
                    onNewChat = {},
                    onOpenHistory = {},
                    onDismissError = {},
                    bottomBar = { navBar(SignedInTab.Chat) },
                )
            }
        }
        capture("chat-error")
    }

    @Test
    fun chatReadOnlyTaskRun() {
        val state = ChatUiState(
            conversationId = "c2",
            readOnly = true,
            messages = listOf(
                ChatMessage(
                    0,
                    role = "assistant",
                    text = "Gold moved **1.4%** in the last hour. The umbrella notice " +
                        "is unchanged, so nothing was pushed.",
                ),
            ),
        )
        compose.setContent {
            WinnieTheme {
                ChatScreen(
                    ui = state,
                    onInputChange = {},
                    onSend = {},
                    onNewChat = {},
                    onOpenHistory = {},
                    onDismissError = {},
                    bottomBar = { navBar(SignedInTab.Chat) },
                )
            }
        }
        capture("chat-read-only")
    }

    @Test
    fun conversationHistory() {
        val state = ChatUiState(
            conversationId = "c1",
            history = listOf(
                AssistantConversationSummary(
                    id = "c1",
                    title = "Weekly review",
                    createdAt = "2026-11-01T09:30:00",
                    source = "user",
                    channel = "api",
                    messageCount = 6,
                    preview = "Here is the summary of your week…",
                ),
                AssistantConversationSummary(
                    id = "c2",
                    title = "Morning brief",
                    createdAt = "2026-10-31T07:00:00",
                    source = "task",
                    channel = "api",
                    messageCount = 1,
                    preview = "Gold moved 1.4% in the last hour.",
                ),
            ),
        )
        compose.setContent {
            WinnieTheme {
                ChatHistoryScreen(
                    ui = state,
                    onOpen = {},
                    onNewChat = {},
                    onRefresh = {},
                    onBack = {},
                )
            }
        }
        capture("chat-history")
    }

    @Test
    fun phoneDataTab() {
        val app = ApplicationProvider.getApplicationContext<WinnieApplication>()
        compose.setContent {
            WinnieTheme {
                PhoneDataScreen(
                    container = app.container,
                    bottomBar = { navBar(SignedInTab.Phone) },
                )
            }
        }
        capture("phone-data")
    }

    @Test
    fun account() {
        compose.setContent {
            WinnieTheme {
                AccountScreen(
                    user = User(
                        id = "u1",
                        username = "alice",
                        displayName = null,
                        createdAt = "2026-10-31T00:00:00",
                    ),
                    serverUrl = "https://winnie.example.com",
                    savedPassword = true,
                    onForgetPassword = {},
                    onOpenAlerts = {},
                    onSignOut = {},
                    bottomBar = { navBar(SignedInTab.Account) },
                )
            }
        }
        capture("account")
    }

    @Test
    fun loginWithRememberedForm() {
        compose.setContent {
            WinnieTheme {
                LoginScreen(
                    ui = LoginUiState(
                        serverUrl = DEFAULT_SERVER_URL,
                        username = "alice",
                        password = "correct horse",
                    ),
                    notice = null,
                    onServerUrlChange = {},
                    onRegisterModeChange = {},
                    onUsernameChange = {},
                    onPasswordChange = {},
                    onRememberChange = {},
                    onSubmit = {},
                )
            }
        }
        // The screenshot the owner sees: a prefilled form and the eye icon.
        compose.onNodeWithText("Remember username and password").assertExists()
        capture("login")
    }

    @Test
    @Config(qualifiers = "zh-w360dp-h800dp-xhdpi")
    fun chineseChat() {
        compose.setContent {
            WinnieTheme {
                ChatScreen(
                    ui = ChatUiState(),
                    onInputChange = {},
                    onSend = {},
                    onNewChat = {},
                    onOpenHistory = {},
                    onDismissError = {},
                    bottomBar = { navBar(SignedInTab.Chat) },
                )
            }
        }
        capture("chat-empty-zh")
    }

    @Test
    @Config(qualifiers = "zh-w360dp-h800dp-xhdpi")
    fun chinesePhoneData() {
        val app = ApplicationProvider.getApplicationContext<WinnieApplication>()
        compose.setContent {
            WinnieTheme {
                PhoneDataScreen(
                    container = app.container,
                    bottomBar = { navBar(SignedInTab.Phone) },
                )
            }
        }
        capture("phone-data-zh")
    }

    @Test
    @Config(qualifiers = "zh-w360dp-h800dp-xhdpi")
    fun chineseAlerts() {
        val app = ApplicationProvider.getApplicationContext<WinnieApplication>()
        compose.setContent {
            WinnieTheme {
                PushSettingsScreen(container = app.container, onBack = {})
            }
        }
        capture("alerts-zh")
    }

    @Test
    @Config(qualifiers = "zh-w360dp-h800dp-xhdpi")
    fun chineseAccount() {
        compose.setContent {
            WinnieTheme {
                AccountScreen(
                    user = User(
                        id = "u1",
                        username = "alice",
                        displayName = null,
                        createdAt = "2026-10-31T00:00:00",
                    ),
                    serverUrl = "https://winnie.example.com",
                    savedPassword = true,
                    onForgetPassword = {},
                    onOpenAlerts = {},
                    onSignOut = {},
                    bottomBar = { navBar(SignedInTab.Account) },
                )
            }
        }
        capture("account-zh")
    }

    @Test
    fun signOutConfirmation() {
        compose.setContent {
            WinnieTheme {
                AccountScreen(
                    user = User(
                        id = "u1",
                        username = "alice",
                        displayName = null,
                        createdAt = "2026-10-31T00:00:00",
                    ),
                    serverUrl = "https://winnie.example.com",
                    savedPassword = true,
                    onForgetPassword = {},
                    onOpenAlerts = {},
                    onSignOut = {},
                    bottomBar = { navBar(SignedInTab.Account) },
                )
            }
        }
        compose.onNodeWithText("Sign out").performClick()
        capture("account-signout-dialog")
    }
}
