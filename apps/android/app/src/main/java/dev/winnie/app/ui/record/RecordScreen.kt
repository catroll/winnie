package dev.winnie.app.ui.record

import android.Manifest
import android.content.pm.PackageManager
import androidx.activity.compose.BackHandler
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.core.content.ContextCompat
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.viewmodel.compose.viewModel
import androidx.lifecycle.viewmodel.initializer
import androidx.lifecycle.viewmodel.viewModelFactory
import dev.winnie.app.AppContainer
import dev.winnie.app.R
import dev.winnie.app.ui.defaultMeetingTitle
import dev.winnie.app.ui.formatElapsed

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun RecordScreen(
    container: AppContainer,
    onClose: () -> Unit,
    onFinished: (String) -> Unit,
) {
    val viewModel: RecordViewModel = viewModel(
        factory = viewModelFactory {
            initializer {
                RecordViewModel(container.sessions, container.newRecorder())
            }
        },
    )
    val state by viewModel.state.collectAsStateWithLifecycle()
    val context = LocalContext.current
    var permissionDenied by remember { mutableStateOf(false) }
    var title by rememberSaveable { mutableStateOf(defaultMeetingTitle()) }

    val permissionLauncher = rememberLauncherForActivityResult(
        ActivityResultContracts.RequestPermission(),
    ) { granted ->
        permissionDenied = !granted
        if (granted) viewModel.start(title)
    }

    val working = state is RecordUiState.Working
    BackHandler(enabled = true) {
        if (working) return@BackHandler
        viewModel.discard()
        onClose()
    }

    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text(stringResource(R.string.record_new_meeting)) },
                navigationIcon = {
                    TextButton(
                        onClick = {
                            viewModel.discard()
                            onClose()
                        },
                        enabled = !working,
                    ) {
                        Text(stringResource(R.string.record_close))
                    }
                },
            )
        },
    ) { padding ->
        Column(
            modifier = Modifier
                .fillMaxSize()
                .padding(padding)
                .imePadding()
                // The keyboard must not cover the title field on a short screen;
                // the column scrolls instead, and stays centred when it fits.
                .verticalScroll(rememberScrollState())
                .padding(horizontal = 24.dp, vertical = 24.dp),
            horizontalAlignment = Alignment.CenterHorizontally,
            verticalArrangement = Arrangement.Center,
        ) {
            when (val current = state) {
                is RecordUiState.Idle -> {
                    Text(
                        text = stringResource(R.string.record_start_title),
                        style = MaterialTheme.typography.headlineSmall,
                    )
                    Spacer(Modifier.height(8.dp))
                    Text(
                        text = stringResource(R.string.record_description),
                        style = MaterialTheme.typography.bodyMedium,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                        textAlign = TextAlign.Center,
                    )
                    Spacer(Modifier.height(24.dp))
                    OutlinedTextField(
                        value = title,
                        onValueChange = { title = it },
                        label = { Text(stringResource(R.string.record_title)) },
                        singleLine = true,
                        modifier = Modifier.fillMaxWidth(),
                    )
                    if (permissionDenied) {
                        Spacer(Modifier.height(12.dp))
                        Text(
                            text = stringResource(R.string.record_mic_required),
                            color = MaterialTheme.colorScheme.error,
                            style = MaterialTheme.typography.bodyMedium,
                        )
                    }
                    current.error?.let { error ->
                        Spacer(Modifier.height(12.dp))
                        Text(
                            text = error,
                            color = MaterialTheme.colorScheme.error,
                            style = MaterialTheme.typography.bodyMedium,
                        )
                    }
                    Spacer(Modifier.height(24.dp))
                    Button(
                        onClick = {
                            val granted = ContextCompat.checkSelfPermission(
                                context,
                                Manifest.permission.RECORD_AUDIO,
                            ) == PackageManager.PERMISSION_GRANTED
                            if (granted) {
                                viewModel.start(title)
                            } else {
                                permissionLauncher.launch(Manifest.permission.RECORD_AUDIO)
                            }
                        },
                    ) {
                        Text(stringResource(R.string.record_start))
                    }
                }

                is RecordUiState.Recording -> {
                    val scroll = rememberScrollState()
                    Box(
                        modifier = Modifier
                            .size(12.dp)
                            .background(MaterialTheme.colorScheme.error, CircleShape),
                    )
                    Spacer(Modifier.height(12.dp))
                    Text(
                        text = formatElapsed(current.elapsedMs),
                        style = MaterialTheme.typography.displayMedium,
                    )
                    Spacer(Modifier.height(8.dp))
                    Text(
                        text = stringResource(R.string.record_recording),
                        style = MaterialTheme.typography.titleMedium,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                    Spacer(Modifier.height(20.dp))
                    Card(modifier = Modifier.fillMaxWidth()) {
                        Column(Modifier.padding(16.dp)) {
                            Text(
                                text = stringResource(R.string.record_live_captions),
                                style = MaterialTheme.typography.titleSmall,
                            )
                            Spacer(Modifier.height(6.dp))
                            when {
                                current.liveText.isNotBlank() -> {
                                    LaunchedEffect(current.liveText) {
                                        scroll.scrollTo(scroll.maxValue)
                                    }
                                    Text(
                                        text = current.liveText,
                                        style = MaterialTheme.typography.bodyLarge,
                                        modifier = Modifier
                                            .heightIn(max = 220.dp)
                                            .verticalScroll(scroll),
                                    )
                                    if (current.liveState == LiveState.Unavailable) {
                                        Spacer(Modifier.height(6.dp))
                                        Text(
                                            text = current.liveError
                                                ?: stringResource(R.string.record_live_stopped),
                                            color = MaterialTheme.colorScheme.error,
                                            style = MaterialTheme.typography.bodySmall,
                                        )
                                    }
                                }
                                current.liveState == LiveState.Connecting -> {
                                    Text(
                                        text = stringResource(R.string.record_connecting),
                                        style = MaterialTheme.typography.bodyMedium,
                                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                                    )
                                }
                                current.liveState == LiveState.Streaming -> {
                                    Text(
                                        text = stringResource(R.string.record_listening),
                                        style = MaterialTheme.typography.bodyMedium,
                                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                                    )
                                }
                                else -> {
                                    Text(
                                        text = current.liveError
                                            ?: stringResource(R.string.record_live_unavailable),
                                        style = MaterialTheme.typography.bodyMedium,
                                        color = MaterialTheme.colorScheme.error,
                                    )
                                }
                            }
                        }
                    }
                    Spacer(Modifier.height(6.dp))
                    Text(
                        text = stringResource(R.string.record_live_provisional),
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                        textAlign = TextAlign.Center,
                    )
                    Spacer(Modifier.height(24.dp))
                    Button(onClick = viewModel::stopAndProcess) {
                        Text(stringResource(R.string.record_stop_transcribe))
                    }
                    Spacer(Modifier.height(8.dp))
                    TextButton(onClick = viewModel::discard) {
                        Text(stringResource(R.string.record_discard))
                    }
                }

                is RecordUiState.Working -> {
                    CircularProgressIndicator()
                    Spacer(Modifier.height(24.dp))
                    Text(
                        text = current.message,
                        style = MaterialTheme.typography.bodyLarge,
                        textAlign = TextAlign.Center,
                    )
                    Spacer(Modifier.height(8.dp))
                    Text(
                        text = stringResource(R.string.record_keep_open),
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                        textAlign = TextAlign.Center,
                    )
                }

                is RecordUiState.Failed -> {
                    Text(
                        text = stringResource(R.string.record_create_failed),
                        style = MaterialTheme.typography.titleLarge,
                    )
                    Spacer(Modifier.height(12.dp))
                    Text(
                        text = current.message,
                        style = MaterialTheme.typography.bodyMedium,
                        color = MaterialTheme.colorScheme.error,
                        textAlign = TextAlign.Center,
                    )
                    Spacer(Modifier.height(24.dp))
                    Button(onClick = viewModel::discard) {
                        Text(stringResource(R.string.record_back_to_start))
                    }
                }

                is RecordUiState.Finished -> {
                    LaunchedEffect(current.meetingId) {
                        // Reset before navigating so reopening the recorder does
                        // not immediately navigate to the previous meeting.
                        viewModel.reset()
                        onFinished(current.meetingId)
                    }
                    CircularProgressIndicator()
                }
            }
        }
    }
}
