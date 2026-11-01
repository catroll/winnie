package dev.winnie.app.ui.push

import android.Manifest
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Build
import android.provider.Settings
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Surface
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.viewmodel.compose.viewModel
import androidx.lifecycle.viewmodel.initializer
import androidx.lifecycle.viewmodel.viewModelFactory
import dev.winnie.app.AppContainer
import dev.winnie.app.R

/**
 * Alerts and the background connection.
 *
 * The screen is explicit about the trade: a self-hosted socket means no vendor
 * push service, but it also means a permanent notification and battery use, and
 * some phone vendors kill it anyway unless battery optimization is disabled.
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun PushSettingsScreen(
    container: AppContainer,
    onBack: () -> Unit,
) {
    val appContext = LocalContext.current.applicationContext
    val viewModel: PushSettingsViewModel = viewModel(
        factory = viewModelFactory {
            initializer { PushSettingsViewModel(container, appContext) }
        },
    )
    val ui by viewModel.ui.collectAsStateWithLifecycle()

    val permissionLauncher = rememberLauncherForActivityResult(
        ActivityResultContracts.RequestPermission(),
    ) { granted ->
        if (granted) {
            viewModel.setEnabled(true)
        } else {
            viewModel.permissionDenied()
        }
    }

    Scaffold(
        topBar = {
            TopAppBar(
                title = {
                    Text(
                        text = stringResource(R.string.alerts_title),
                        maxLines = 1,
                        softWrap = false,
                        overflow = TextOverflow.Ellipsis,
                    )
                },
                navigationIcon = {
                    IconButton(onClick = onBack) {
                        Icon(painterResource(R.drawable.ic_back), contentDescription = "Back")
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
        LazyColumn(
            modifier = Modifier
                .fillMaxSize()
                .padding(padding),
            contentPadding = PaddingValues(16.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            item { Explanation() }
            item {
                ConnectionCard(
                    ui = ui,
                    onToggle = { enabled ->
                        if (!enabled) {
                            viewModel.setEnabled(false)
                        } else if (needsNotificationPermission(appContext, ui)) {
                            permissionLauncher.launch(Manifest.permission.POST_NOTIFICATIONS)
                        } else {
                            viewModel.setEnabled(true)
                        }
                    },
                )
            }
            item { TestCard(ui, onSend = viewModel::sendTest) }
            item {
                PermissionCard(
                    ui = ui,
                    onOpenNotificationSettings = { openNotificationSettings(appContext) },
                    onOpenBatterySettings = { openBatterySettings(appContext) },
                )
            }
            item { ServerCard(ui) }
            item { BackgroundNote() }
            ui.message?.let { message ->
                item {
                    Text(
                        message,
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
            }
        }
    }
}

@Composable
private fun Explanation() {
    Surface(
        color = MaterialTheme.colorScheme.surfaceVariant,
        shape = MaterialTheme.shapes.medium,
    ) {
        Column(Modifier.padding(16.dp)) {
            Text(stringResource(R.string.alerts_how_title), style = MaterialTheme.typography.titleSmall)
            Spacer(Modifier.height(6.dp))
            Text(
                stringResource(R.string.alerts_how_body),
                style = MaterialTheme.typography.bodySmall,
            )
            Spacer(Modifier.height(6.dp))
            Text(
                stringResource(R.string.alerts_no_vendor),
                style = MaterialTheme.typography.bodySmall,
            )
        }
    }
}

@Composable
private fun ConnectionCard(ui: PushSettingsUi, onToggle: (Boolean) -> Unit) {
    Card {
        Column(Modifier.padding(16.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Column(Modifier.weight(1f)) {
                    Text(
                        stringResource(R.string.alerts_connection),
                        style = MaterialTheme.typography.titleMedium,
                    )
                    Text(
                        connectionStatusLabel(ui),
                        style = MaterialTheme.typography.bodySmall,
                        color = if (ui.connection == "connected") {
                            MaterialTheme.colorScheme.primary
                        } else {
                            MaterialTheme.colorScheme.onSurfaceVariant
                        },
                    )
                }
                Switch(checked = ui.enabled, onCheckedChange = onToggle)
            }
            if (ui.error.isNotBlank()) {
                Spacer(Modifier.height(8.dp))
                Text(
                    ui.error,
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.error,
                )
            }
            Text(
                stringResource(R.string.alerts_last_event, ui.lastEventLabel, ui.lastSeq),
                style = MaterialTheme.typography.labelSmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
    }
}

@Composable
private fun TestCard(ui: PushSettingsUi, onSend: () -> Unit) {
    Card {
        Column(Modifier.padding(16.dp)) {
            Text(stringResource(R.string.alerts_check_title), style = MaterialTheme.typography.titleMedium)
            Text(
                stringResource(R.string.alerts_check_body),
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            Spacer(Modifier.height(12.dp))
            Button(onClick = onSend, enabled = ui.enabled) {
                Text(stringResource(R.string.alerts_send_test))
            }
        }
    }
}

@Composable
private fun PermissionCard(
    ui: PushSettingsUi,
    onOpenNotificationSettings: () -> Unit,
    onOpenBatterySettings: () -> Unit,
) {
    Card {
        Column(Modifier.padding(16.dp)) {
            Text(
                stringResource(R.string.alerts_permissions),
                style = MaterialTheme.typography.titleMedium,
            )
            PermissionRow(
                title = stringResource(R.string.alerts_notifications),
                detail = stringResource(
                    if (ui.notificationsAllowed) {
                        R.string.alerts_notifications_allowed
                    } else {
                        R.string.alerts_notifications_blocked
                    },
                ),
                ok = ui.notificationsAllowed,
                action = stringResource(R.string.alerts_settings),
                onOpen = onOpenNotificationSettings,
            )
            PermissionRow(
                title = stringResource(R.string.alerts_battery),
                detail = stringResource(
                    if (ui.batteryUnrestricted) {
                        R.string.alerts_battery_unrestricted
                    } else {
                        R.string.alerts_battery_optimized
                    },
                ),
                ok = ui.batteryUnrestricted,
                action = stringResource(R.string.alerts_unrestrict),
                onOpen = onOpenBatterySettings,
            )
        }
    }
}

@Composable
private fun PermissionRow(
    title: String,
    detail: String,
    ok: Boolean,
    action: String,
    onOpen: () -> Unit,
) {
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .padding(vertical = 8.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Column(Modifier.weight(1f)) {
            Text(title, style = MaterialTheme.typography.bodyMedium)
            Text(
                detail,
                style = MaterialTheme.typography.labelSmall,
                color = if (ok) {
                    MaterialTheme.colorScheme.primary
                } else {
                    MaterialTheme.colorScheme.error
                },
            )
        }
        TextButton(onClick = onOpen) { Text(action) }
    }
}

@Composable
private fun ServerCard(ui: PushSettingsUi) {
    Card {
        Column(Modifier.padding(16.dp)) {
            Text(stringResource(R.string.alerts_server), style = MaterialTheme.typography.titleMedium)
            Text(
                stringResource(
                    R.string.alerts_server_counts,
                    ui.serverConnected,
                    ui.pending,
                ),
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            Text(
                stringResource(R.string.alerts_server_body),
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
    }
}

@Composable
private fun BackgroundNote() {
    Text(
        stringResource(R.string.alerts_troubleshoot),
        style = MaterialTheme.typography.bodySmall,
        color = MaterialTheme.colorScheme.onSurfaceVariant,
    )
}

@Composable
private fun connectionStatusLabel(ui: PushSettingsUi): String {
    if (!ui.enabled) return stringResource(R.string.alerts_state_off)
    return when (ui.connection) {
        "connected" -> stringResource(R.string.alerts_state_connected)
        "connecting" -> stringResource(R.string.alerts_state_connecting)
        "signed out" -> stringResource(R.string.alerts_state_signed_out)
        "blocked" -> stringResource(R.string.alerts_state_blocked)
        "stopped" -> stringResource(R.string.alerts_state_stopped)
        else -> stringResource(R.string.alerts_state_disconnected)
    }
}

private fun needsNotificationPermission(context: Context, ui: PushSettingsUi): Boolean {
    if (Build.VERSION.SDK_INT < Build.VERSION_CODES.TIRAMISU) return false
    return !ui.notificationsAllowed &&
        context.checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) !=
        android.content.pm.PackageManager.PERMISSION_GRANTED
}

private fun openNotificationSettings(context: Context) {
    val intent = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
        Intent(Settings.ACTION_APP_NOTIFICATION_SETTINGS)
            .putExtra(Settings.EXTRA_APP_PACKAGE, context.packageName)
    } else {
        Intent(
            Settings.ACTION_APPLICATION_DETAILS_SETTINGS,
            Uri.parse("package:${context.packageName}"),
        )
    }
    intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
    runCatching { context.startActivity(intent) }
}

private fun openBatterySettings(context: Context) {
    val request = Intent(
        Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS,
        Uri.parse("package:${context.packageName}"),
    ).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
    runCatching { context.startActivity(request) }.onFailure {
        val fallback = Intent(Settings.ACTION_IGNORE_BATTERY_OPTIMIZATION_SETTINGS)
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        runCatching { context.startActivity(fallback) }
    }
}
