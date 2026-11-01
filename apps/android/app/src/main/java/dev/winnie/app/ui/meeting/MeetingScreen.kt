package dev.winnie.app.ui.meeting

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.Button
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ElevatedCard
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.font.FontStyle
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.viewmodel.compose.viewModel
import androidx.lifecycle.viewmodel.initializer
import androidx.lifecycle.viewmodel.viewModelFactory
import dev.winnie.app.AppContainer
import dev.winnie.app.R
import dev.winnie.app.data.ActionItem
import dev.winnie.app.data.Decision
import dev.winnie.app.data.Meeting
import dev.winnie.app.data.Minutes
import dev.winnie.app.data.Segment
import dev.winnie.app.data.Topic
import dev.winnie.app.ui.StatusPill
import dev.winnie.app.ui.formatMeetingTime
import dev.winnie.app.ui.formatOffset
import dev.winnie.app.ui.statusLabel

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun MeetingScreen(
    container: AppContainer,
    meetingId: String,
    onBack: () -> Unit,
) {
    val viewModel: MeetingViewModel = viewModel(
        key = meetingId,
        factory = viewModelFactory {
            initializer { MeetingViewModel(container.sessions, meetingId) }
        },
    )
    val state by viewModel.state.collectAsStateWithLifecycle()
    val meeting = state.meeting

    Scaffold(
        topBar = {
            TopAppBar(
                title = {
                    Text(
                        text = meeting?.title ?: stringResource(R.string.meeting_fallback_title),
                        maxLines = 1,
                        overflow = TextOverflow.Ellipsis,
                    )
                },
                navigationIcon = {
                    IconButton(onClick = onBack) {
                        Icon(
                        painterResource(R.drawable.ic_back),
                        contentDescription = stringResource(R.string.common_back),
                    )
                    }
                },
                actions = {
                    IconButton(onClick = viewModel::refresh) {
                        Icon(
                            painterResource(R.drawable.ic_refresh),
                            contentDescription = stringResource(R.string.common_refresh),
                        )
                    }
                },
            )
        },
    ) { padding ->
        Box(
            modifier = Modifier
                .fillMaxSize()
                .padding(padding),
        ) {
            when {
                state.loading && meeting == null -> {
                    CircularProgressIndicator(Modifier.align(Alignment.Center))
                }

                meeting == null -> {
                    Column(
                        modifier = Modifier
                            .align(Alignment.Center)
                            .padding(32.dp),
                        horizontalAlignment = Alignment.CenterHorizontally,
                    ) {
                        Text(
                            text = state.error
                                ?: stringResource(R.string.meeting_load_failed),
                            color = MaterialTheme.colorScheme.error,
                        )
                        Spacer(Modifier.height(16.dp))
                        Button(onClick = viewModel::refresh) {
                            Text(stringResource(R.string.common_retry))
                        }
                    }
                }

                else -> {
                    LazyColumn(
                        contentPadding = PaddingValues(bottom = 32.dp),
                    ) {
                        item { MeetingHeader(meeting) }

                        if (meeting.isProcessing) {
                            item {
                                Column(Modifier.padding(horizontal = 16.dp)) {
                                    Text(
                                        text = stringResource(
                                            R.string.meeting_processing,
                                            statusLabel(meeting.stage ?: meeting.status),
                                        ),
                                        style = MaterialTheme.typography.bodyMedium,
                                    )
                                    Spacer(Modifier.height(8.dp))
                                    LinearProgressIndicator(Modifier.fillMaxWidth())
                                }
                            }
                        }

                        if (state.error != null && !meeting.isFailed) {
                            item {
                                Text(
                                    text = state.error.orEmpty(),
                                    color = MaterialTheme.colorScheme.error,
                                    style = MaterialTheme.typography.bodyMedium,
                                    modifier = Modifier.padding(16.dp),
                                )
                            }
                        }

                        state.minutes?.let { minutes ->
                            item { MinutesCard(minutes) }
                        }

                        if (meeting.isReady) {
                            item {
                                Text(
                                    text = stringResource(R.string.meeting_transcript),
                                    style = MaterialTheme.typography.titleMedium,
                                    modifier = Modifier.padding(
                                        start = 16.dp,
                                        end = 16.dp,
                                        top = 16.dp,
                                        bottom = 8.dp,
                                    ),
                                )
                            }
                            if (state.segments.isEmpty()) {
                                item {
                                    Text(
                                        text = stringResource(R.string.meeting_no_transcript),
                                        style = MaterialTheme.typography.bodyMedium,
                                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                                        modifier = Modifier.padding(16.dp),
                                    )
                                }
                            } else {
                                items(state.segments, key = { it.seq }) { segment ->
                                    SegmentRow(segment)
                                    HorizontalDivider(
                                        modifier = Modifier.padding(horizontal = 16.dp),
                                    )
                                }
                            }
                        }
                    }
                }
            }
        }
    }
}

@Composable
private fun MeetingHeader(meeting: Meeting) {
    Column(modifier = Modifier.padding(16.dp)) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            StatusPill(meeting.status)
            Spacer(Modifier.width(12.dp))
            Text(
                text = formatMeetingTime(meeting.createdAt),
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
        if (meeting.isFailed && meeting.error != null) {
            Spacer(Modifier.height(8.dp))
            Text(
                text = meeting.error,
                color = MaterialTheme.colorScheme.error,
                style = MaterialTheme.typography.bodyMedium,
            )
        }
    }
}

@Composable
private fun SegmentRow(segment: Segment) {
    Column(
        modifier = Modifier
            .fillMaxWidth()
            .padding(horizontal = 16.dp, vertical = 10.dp),
    ) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text(
                text = formatOffset(segment.startMs),
                style = MaterialTheme.typography.labelSmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            val language = segment.detectedLanguage
            if (!language.isNullOrBlank()) {
                Spacer(Modifier.width(8.dp))
                Text(
                    text = language.uppercase(),
                    style = MaterialTheme.typography.labelSmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
        }
        Spacer(Modifier.height(4.dp))
        Text(
            text = segment.text,
            style = MaterialTheme.typography.bodyLarge,
        )
        val translation = segment.translatedText
        if (!translation.isNullOrBlank()) {
            Spacer(Modifier.height(4.dp))
            Text(
                text = translation,
                style = MaterialTheme.typography.bodyMedium,
                fontStyle = FontStyle.Italic,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
    }
}

@Composable
private fun MinutesCard(minutes: Minutes) {
    ElevatedCard(
        modifier = Modifier
            .fillMaxWidth()
            .padding(16.dp),
    ) {
        Column(
            modifier = Modifier.padding(16.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            Text(
                text = stringResource(R.string.meeting_minutes),
                style = MaterialTheme.typography.titleLarge,
            )
            Text(
                text = minutes.summary,
                style = MaterialTheme.typography.bodyMedium,
            )

            if (minutes.topics.isNotEmpty()) {
                MinutesSection(stringResource(R.string.meeting_topics)) {
                    minutes.topics.forEach { TopicBlock(it) }
                }
            }
            if (minutes.decisions.isNotEmpty()) {
                MinutesSection(stringResource(R.string.meeting_decisions)) {
                    minutes.decisions.forEach { DecisionBlock(it) }
                }
            }
            if (minutes.actionItems.isNotEmpty()) {
                MinutesSection(stringResource(R.string.meeting_action_items)) {
                    minutes.actionItems.forEach { ActionItemBlock(it) }
                }
            }
        }
    }
}

@Composable
private fun MinutesSection(title: String, content: @Composable () -> Unit) {
    Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
        Text(
            text = title,
            style = MaterialTheme.typography.titleSmall,
            color = MaterialTheme.colorScheme.primary,
        )
        content()
    }
}

@Composable
private fun TopicBlock(topic: Topic) {
    Column {
        Text(text = topic.title, style = MaterialTheme.typography.bodyMedium)
        topic.points.forEach { point ->
            Text(
                text = "• $point",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
    }
}

@Composable
private fun DecisionBlock(decision: Decision) {
    Column {
        Text(text = decision.text, style = MaterialTheme.typography.bodyMedium)
        if (decision.context.isNotBlank()) {
            Text(
                text = decision.context,
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
    }
}

@Composable
private fun ActionItemBlock(item: ActionItem) {
    Column {
        Text(text = item.text, style = MaterialTheme.typography.bodyMedium)
        val meta = listOfNotNull(
            item.owner?.takeIf { it.isNotBlank() }?.let {
                stringResource(R.string.meeting_owner, it)
            },
            item.dueDate?.takeIf { it.isNotBlank() }?.let {
                stringResource(R.string.meeting_due, it)
            },
        )
        if (meta.isNotEmpty()) {
            Text(
                text = meta.joinToString("  ·  "),
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
    }
}
