package dev.winnie.app.ui.chat

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.AssistChip
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.FilledIconButton
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.rotate
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontStyle
import androidx.compose.ui.text.input.KeyboardCapitalization
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import com.mikepenz.markdown.m3.Markdown
import com.mikepenz.markdown.m3.markdownTypography
import com.mikepenz.markdown.model.rememberMarkdownState
import dev.winnie.app.R

private val SUGGESTIONS = listOf(
    R.string.chat_suggestion_schedule,
    R.string.chat_suggestion_meeting,
    R.string.chat_suggestion_phone,
)

/**
 * The assistant tab: a conversation, its composer, and two actions in the top
 * bar.
 *
 * The title is a single line with no title-adjacent buttons, which is the fix
 * for the old home screen where four text actions squeezed "Winnie" onto two
 * lines. Everything that used to live there has a home now: chat, meetings,
 * phone data and the account each own a bottom-bar tab.
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun ChatScreen(
    ui: ChatUiState,
    onInputChange: (String) -> Unit,
    onSend: () -> Unit,
    onNewChat: () -> Unit,
    onOpenHistory: () -> Unit,
    onDismissError: () -> Unit,
    bottomBar: @Composable () -> Unit,
    modifier: Modifier = Modifier,
) {
    val listState = rememberLazyListState()

    // Keep the newest line visible, both for a transcript that just loaded and
    // while a reply is pending.
    LaunchedEffect(ui.messages.size, ui.sending, ui.loading) {
        if (ui.messages.isNotEmpty()) {
            listState.animateScrollToItem(ui.messages.lastIndex)
        }
    }

    Scaffold(
        modifier = modifier.imePadding(),
        topBar = {
            TopAppBar(
                title = {
                    Text(
                        text = stringResource(R.string.chat_title),
                        maxLines = 1,
                        softWrap = false,
                        overflow = TextOverflow.Ellipsis,
                        style = MaterialTheme.typography.titleLarge,
                    )
                },
                actions = {
                    IconButton(
                        onClick = onOpenHistory,
                        enabled = !ui.sending,
                        modifier = Modifier.testTag("openHistory"),
                    ) {
                        Icon(
                            painterResource(R.drawable.ic_history),
                            contentDescription = stringResource(
                                R.string.chat_history_description,
                            ),
                        )
                    }
                    IconButton(
                        onClick = onNewChat,
                        enabled = !ui.sending,
                        modifier = Modifier.testTag("newChat"),
                    ) {
                        Icon(
                            painterResource(R.drawable.ic_new_chat),
                            contentDescription = stringResource(
                                R.string.chat_new_chat_description,
                            ),
                        )
                    }
                },
            )
        },
        bottomBar = bottomBar,
    ) { padding ->
        Column(
            modifier = Modifier
                .fillMaxSize()
                .padding(padding),
        ) {
            Box(modifier = Modifier.weight(1f)) {
                when {
                    ui.loading -> CircularProgressIndicator(
                        modifier = Modifier.align(Alignment.Center),
                    )

                    ui.messages.isEmpty() -> EmptyChat(
                        onSuggestion = onInputChange,
                        modifier = Modifier.align(Alignment.Center),
                    )

                    else -> MessageList(
                        messages = ui.messages,
                        thinking = ui.sending,
                        listState = listState,
                    )
                }
            }

            if (ui.error != null) {
                ErrorBanner(message = ui.error, onDismiss = onDismissError)
            }

            if (ui.readOnly) {
                ReadOnlyNotice(onNewChat = onNewChat)
            } else {
                Composer(
                    input = ui.input,
                    enabled = !ui.sending && !ui.loading,
                    onInputChange = onInputChange,
                    onSend = onSend,
                )
            }
        }
    }
}

/**
 * A scheduled task run is a record, not a conversation: the server refuses a
 * message in one. Offering the composer would produce a 409, so the bar says so
 * and offers the only useful action.
 */
@Composable
private fun ReadOnlyNotice(onNewChat: () -> Unit) {
    Surface(tonalElevation = 3.dp) {
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .padding(horizontal = 16.dp, vertical = 8.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Text(
                text = stringResource(R.string.chat_read_only),
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                modifier = Modifier.weight(1f),
            )
            TextButton(onClick = onNewChat) {
                Text(stringResource(R.string.common_new_chat))
            }
        }
    }
}

@Composable
private fun MessageList(
    messages: List<ChatMessage>,
    thinking: Boolean,
    listState: androidx.compose.foundation.lazy.LazyListState,
) {
    LazyColumn(
        state = listState,
        modifier = Modifier.fillMaxSize(),
        contentPadding = PaddingValues(horizontal = 12.dp, vertical = 12.dp),
        verticalArrangement = Arrangement.spacedBy(10.dp),
    ) {
        items(messages, key = { it.key }) { message ->
            when {
                message.isTool -> ToolMessage(message)
                message.fromUser -> UserMessage(message)
                else -> AssistantMessage(message)
            }
        }
        if (thinking) {
            item(key = "thinking") { ThinkingRow() }
        }
    }
}

@Composable
private fun UserMessage(message: ChatMessage) {
    Row(modifier = Modifier.fillMaxWidth()) {
        Spacer(Modifier.width(40.dp))
        Box(modifier = Modifier.weight(1f), contentAlignment = Alignment.CenterEnd) {
            Surface(
                color = MaterialTheme.colorScheme.primary,
                contentColor = MaterialTheme.colorScheme.onPrimary,
                shape = RoundedCornerShape(
                    topStart = 18.dp,
                    topEnd = 18.dp,
                    bottomEnd = 4.dp,
                    bottomStart = 18.dp,
                ),
            ) {
                Text(
                    text = message.text,
                    style = MaterialTheme.typography.bodyLarge,
                    modifier = Modifier.padding(horizontal = 14.dp, vertical = 10.dp),
                )
            }
        }
    }
}

@Composable
private fun AssistantMessage(message: ChatMessage) {
    Row(modifier = Modifier.fillMaxWidth()) {
        Surface(
            modifier = Modifier.weight(1f),
            color = MaterialTheme.colorScheme.surfaceContainerHigh,
            contentColor = MaterialTheme.colorScheme.onSurface,
            shape = RoundedCornerShape(
                topStart = 4.dp,
                topEnd = 18.dp,
                bottomEnd = 18.dp,
                bottomStart = 18.dp,
            ),
        ) {
            Column(modifier = Modifier.padding(horizontal = 14.dp, vertical = 10.dp)) {
                val markdownState = rememberMarkdownState(
                    content = message.text,
                    // Chat messages are short; parsing immediately avoids a
                    // blank frame in the bubble.
                    immediate = true,
                )
                Markdown(
                    markdownState,
                    typography = chatMarkdownTypography(),
                )
                if (message.tools.isNotEmpty()) {
                    Spacer(Modifier.height(8.dp))
                    Text(
                        text = stringResource(
                            R.string.chat_used_tools,
                            message.tools.joinToString(", "),
                        ),
                        style = MaterialTheme.typography.labelSmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
            }
        }
        Spacer(Modifier.width(20.dp))
    }
}

/**
 * The library's default heading sizes are display-scale, which is right for an
 * article and wrong inside a chat bubble: `##` would render at 45sp. This maps
 * headings onto the app's title styles and keeps body text at the body size.
 */
@Composable
private fun chatMarkdownTypography() = markdownTypography(
    h1 = MaterialTheme.typography.titleLarge,
    h2 = MaterialTheme.typography.titleLarge,
    h3 = MaterialTheme.typography.titleMedium,
    h4 = MaterialTheme.typography.titleSmall,
    h5 = MaterialTheme.typography.titleSmall,
    h6 = MaterialTheme.typography.titleSmall,
    text = MaterialTheme.typography.bodyLarge,
    paragraph = MaterialTheme.typography.bodyLarge,
    ordered = MaterialTheme.typography.bodyLarge,
    bullet = MaterialTheme.typography.bodyLarge,
    list = MaterialTheme.typography.bodyLarge,
    table = MaterialTheme.typography.bodySmall,
    code = MaterialTheme.typography.bodySmall.copy(fontFamily = FontFamily.Monospace),
    inlineCode = MaterialTheme.typography.bodyMedium.copy(fontFamily = FontFamily.Monospace),
    quote = MaterialTheme.typography.bodyMedium.copy(fontStyle = FontStyle.Italic),
)

/**
 * A tool call's output is a debugging record, not an answer: it stays collapsed
 * unless the owner opens it, so the conversation reads like a conversation.
 */
@Composable
private fun ToolMessage(message: ChatMessage) {
    var expanded by rememberSaveable(message.key) { mutableStateOf(false) }
    Surface(
        modifier = Modifier.fillMaxWidth(),
        color = MaterialTheme.colorScheme.surfaceVariant,
        contentColor = MaterialTheme.colorScheme.onSurfaceVariant,
        shape = RoundedCornerShape(12.dp),
    ) {
        Column(modifier = Modifier.padding(horizontal = 12.dp, vertical = 8.dp)) {
            Row(
                modifier = Modifier.fillMaxWidth(),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Icon(
                    painter = painterResource(R.drawable.ic_chevron_right),
                    contentDescription = null,
                    modifier = Modifier
                        .size(18.dp)
                        .rotate(if (expanded) 90f else 0f),
                )
                Spacer(Modifier.width(4.dp))
                Text(
                    text = stringResource(
                        R.string.chat_tool,
                        message.name.ifBlank {
                            stringResource(R.string.chat_tool_result)
                        },
                    ),
                    style = MaterialTheme.typography.labelMedium,
                    modifier = Modifier.weight(1f),
                )
                TextButton(onClick = { expanded = !expanded }) {
                    Text(
                        stringResource(
                            if (expanded) R.string.common_hide else R.string.common_show,
                        ),
                    )
                }
            }
            if (expanded) {
                Text(
                    text = message.text,
                    style = MaterialTheme.typography.bodySmall.copy(
                        fontFamily = FontFamily.Monospace,
                    ),
                    modifier = Modifier.padding(top = 4.dp),
                )
            }
        }
    }
}

@Composable
private fun ThinkingRow() {
    Row(
        modifier = Modifier.fillMaxWidth(),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Spacer(Modifier.width(20.dp))
        Surface(
            color = MaterialTheme.colorScheme.surfaceContainerHigh,
            shape = RoundedCornerShape(18.dp),
        ) {
            Row(
                modifier = Modifier.padding(horizontal = 14.dp, vertical = 10.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                CircularProgressIndicator(
                    modifier = Modifier.size(14.dp),
                    strokeWidth = 2.dp,
                )
                Spacer(Modifier.width(8.dp))
                Text(
                    text = stringResource(R.string.chat_thinking),
                    style = MaterialTheme.typography.bodyMedium,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
        }
    }
}

@Composable
private fun EmptyChat(
    onSuggestion: (String) -> Unit,
    modifier: Modifier = Modifier,
) {
    Column(
        modifier = modifier.padding(horizontal = 32.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
    ) {
        Icon(
            painter = painterResource(R.drawable.ic_chat),
            contentDescription = null,
            tint = MaterialTheme.colorScheme.primary,
            modifier = Modifier.size(48.dp),
        )
        Spacer(Modifier.height(16.dp))
        Text(
            text = stringResource(R.string.chat_answer_anything),
            style = MaterialTheme.typography.titleLarge,
        )
        Spacer(Modifier.height(8.dp))
        Text(
            text = stringResource(R.string.chat_empty_body),
            style = MaterialTheme.typography.bodyMedium,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
            textAlign = TextAlign.Center,
        )
        Spacer(Modifier.height(20.dp))
        SUGGESTIONS.forEach { suggestion ->
            val label = stringResource(suggestion)
            AssistChip(
                onClick = { onSuggestion(label) },
                label = { Text(label) },
                modifier = Modifier.padding(vertical = 2.dp),
            )
        }
    }
}

@Composable
private fun ErrorBanner(message: String, onDismiss: () -> Unit) {
    Surface(
        color = MaterialTheme.colorScheme.errorContainer,
        contentColor = MaterialTheme.colorScheme.onErrorContainer,
        modifier = Modifier.fillMaxWidth(),
    ) {
        Row(
            modifier = Modifier.padding(start = 16.dp, end = 8.dp, top = 4.dp, bottom = 4.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Text(
                text = message,
                style = MaterialTheme.typography.bodySmall,
                modifier = Modifier.weight(1f),
            )
            TextButton(onClick = onDismiss) {
                Text(stringResource(R.string.common_dismiss))
            }
        }
    }
}

@Composable
private fun Composer(
    input: String,
    enabled: Boolean,
    onInputChange: (String) -> Unit,
    onSend: () -> Unit,
) {
    Surface(tonalElevation = 3.dp) {
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .padding(horizontal = 12.dp, vertical = 8.dp),
            verticalAlignment = Alignment.Bottom,
        ) {
            OutlinedTextField(
                value = input,
                onValueChange = onInputChange,
                enabled = enabled,
                modifier = Modifier
                    .weight(1f)
                    .testTag("chatInput"),
                placeholder = { Text(stringResource(R.string.chat_message_hint)) },
                maxLines = 5,
                shape = RoundedCornerShape(24.dp),
                keyboardOptions = KeyboardOptions(
                    capitalization = KeyboardCapitalization.Sentences,
                ),
            )
            Spacer(Modifier.width(8.dp))
            FilledIconButton(
                onClick = onSend,
                enabled = enabled && input.isNotBlank(),
                modifier = Modifier
                    .padding(bottom = 4.dp)
                    .testTag("chatSend"),
            ) {
                Icon(
                    painterResource(R.drawable.ic_send),
                    contentDescription = stringResource(R.string.common_send),
                )
            }
        }
    }
}
