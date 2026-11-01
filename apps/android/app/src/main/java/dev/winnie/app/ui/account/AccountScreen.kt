package dev.winnie.app.ui.account

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
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
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Card
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.annotation.DrawableRes
import dev.winnie.app.BuildConfig
import dev.winnie.app.R
import dev.winnie.app.data.User

/**
 * The account tab: who is signed in, where the app points, the two settings that
 * are local to this device, and the way out.
 *
 * The sign-in name is not editable because the server does not expose a rename;
 * the server address is not editable here because changing it while a session
 * exists would silently invalidate the token — the login screen owns it.
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun AccountScreen(
    user: User,
    serverUrl: String,
    savedPassword: Boolean,
    onForgetPassword: () -> Unit,
    onOpenAlerts: () -> Unit,
    wakeEnabled: Boolean,
    onWakeChange: (Boolean) -> Unit,
    onSignOut: () -> Unit,
    bottomBar: @Composable () -> Unit,
) {
    var confirmSignOut by remember { mutableStateOf(false) }
    var confirmForget by remember { mutableStateOf(false) }

    Scaffold(
        topBar = {
            TopAppBar(
                title = {
                    Text(
                        text = stringResource(R.string.account_title),
                        maxLines = 1,
                        softWrap = false,
                        overflow = TextOverflow.Ellipsis,
                    )
                },
            )
        },
        bottomBar = bottomBar,
    ) { padding ->
        LazyColumn(
            modifier = Modifier
                .fillMaxSize()
                .padding(padding)
                .testTag("accountList"),
            contentPadding = PaddingValues(16.dp),
            verticalArrangement = Arrangement.spacedBy(16.dp),
        ) {
            item { IdentityCard(user = user, serverUrl = serverUrl) }

            item {
                SectionCard(title = stringResource(R.string.account_this_device)) {
                    SettingRow(
                        title = stringResource(R.string.account_alerts),
                        subtitle = stringResource(R.string.account_alerts_subtitle),
                        icon = R.drawable.ic_alerts,
                        onClick = onOpenAlerts,
                    )
                    HorizontalDivider()
                    WakeRow(enabled = wakeEnabled, onChange = onWakeChange)
                    HorizontalDivider()
                    Row(
                        modifier = Modifier
                            .fillMaxWidth()
                            .padding(vertical = 12.dp),
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        Column(modifier = Modifier.weight(1f)) {
                            Text(
                                text = stringResource(R.string.account_saved_password),
                                style = MaterialTheme.typography.bodyLarge,
                            )
                            Text(
                                text = stringResource(
                                    if (savedPassword) {
                                        R.string.account_password_saved
                                    } else {
                                        R.string.account_password_not_saved
                                    },
                                ),
                                style = MaterialTheme.typography.bodySmall,
                                color = MaterialTheme.colorScheme.onSurfaceVariant,
                            )
                        }
                        if (savedPassword) {
                            TextButton(onClick = { confirmForget = true }) {
                                Text(stringResource(R.string.account_forget))
                            }
                        }
                    }
                }
            }

            item {
                SectionCard(title = stringResource(R.string.account_about)) {
                    Row(
                        modifier = Modifier
                            .fillMaxWidth()
                            .padding(vertical = 12.dp),
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        Text(
                            text = stringResource(R.string.account_app_name),
                            style = MaterialTheme.typography.bodyLarge,
                            modifier = Modifier.weight(1f),
                        )
                        Text(
                            text = BuildConfig.VERSION_NAME,
                            style = MaterialTheme.typography.bodySmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                    }
                }
            }

            item {
                OutlinedButton(
                    onClick = { confirmSignOut = true },
                    modifier = Modifier
                        .fillMaxWidth()
                        .testTag("signOut"),
                ) {
                    Icon(painterResource(R.drawable.ic_logout), contentDescription = null)
                    Spacer(Modifier.width(8.dp))
                    Text(stringResource(R.string.account_sign_out))
                }
            }
        }
    }

    if (confirmSignOut) {
        AlertDialog(
            onDismissRequest = { confirmSignOut = false },
            title = { Text(stringResource(R.string.account_sign_out_title)) },
            text = {
                Text(
                    stringResource(
                        if (savedPassword) {
                            R.string.account_sign_out_body_saved
                        } else {
                            R.string.account_sign_out_body_unsigned
                        },
                    ),
                )
            },
            confirmButton = {
                TextButton(
                    onClick = {
                        confirmSignOut = false
                        onSignOut()
                    },
                    modifier = Modifier.testTag("confirmSignOut"),
                ) { Text(stringResource(R.string.account_sign_out)) }
            },
            dismissButton = {
                TextButton(onClick = { confirmSignOut = false }) {
                    Text(stringResource(R.string.common_cancel))
                }
            },
        )
    }

    if (confirmForget) {
        AlertDialog(
            onDismissRequest = { confirmForget = false },
            title = { Text(stringResource(R.string.account_forget_title)) },
            text = { Text(stringResource(R.string.account_forget_body)) },
            confirmButton = {
                TextButton(
                    onClick = {
                        confirmForget = false
                        onForgetPassword()
                    },
                    modifier = Modifier.testTag("confirmForget"),
                ) { Text(stringResource(R.string.account_forget)) }
            },
            dismissButton = {
                TextButton(onClick = { confirmForget = false }) {
                    Text(stringResource(R.string.common_cancel))
                }
            },
        )
    }
}

@Composable
private fun IdentityCard(user: User, serverUrl: String) {
    Card(modifier = Modifier.fillMaxWidth()) {
        Row(
            modifier = Modifier.padding(16.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Box(
                modifier = Modifier
                    .size(52.dp)
                    .background(MaterialTheme.colorScheme.primaryContainer, CircleShape),
                contentAlignment = Alignment.Center,
            ) {
                Text(
                    text = user.username.take(1).uppercase(),
                    style = MaterialTheme.typography.titleLarge,
                    color = MaterialTheme.colorScheme.onPrimaryContainer,
                )
            }
            Spacer(Modifier.width(16.dp))
            Column(modifier = Modifier.weight(1f)) {
                Text(
                    text = user.displayName?.takeIf { it.isNotBlank() } ?: user.username,
                    style = MaterialTheme.typography.titleMedium,
                )
                Text(
                    text = serverUrl,
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                )
            }
        }
    }
}

@Composable
private fun SectionCard(title: String, content: @Composable () -> Unit) {
    Column {
        Text(
            text = title,
            style = MaterialTheme.typography.labelMedium,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
            modifier = Modifier.padding(start = 4.dp, bottom = 6.dp),
        )
        Card(modifier = Modifier.fillMaxWidth()) {
            Column(modifier = Modifier.padding(horizontal = 16.dp)) { content() }
        }
    }
}

/**
 * The wake word switch.
 *
 * It sits next to the alerts switch because both keep something running on the
 * phone, and both are device-local choices. Unlike the alert connection, this
 * one holds the microphone, so the summary says that in one sentence instead of
 * hiding the cost in documentation.
 */
@Composable
private fun WakeRow(enabled: Boolean, onChange: (Boolean) -> Unit) {
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .padding(vertical = 12.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Column(modifier = Modifier.weight(1f)) {
            Text(
                text = stringResource(R.string.account_wake),
                style = MaterialTheme.typography.bodyLarge,
            )
            Text(
                text = stringResource(R.string.account_wake_subtitle),
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
        Spacer(Modifier.width(12.dp))
        Switch(
            checked = enabled,
            onCheckedChange = onChange,
            modifier = Modifier.testTag("wakeSwitch"),
        )
    }
}

@Composable
private fun SettingRow(
    title: String,
    subtitle: String,
    @DrawableRes icon: Int,
    onClick: () -> Unit,
) {
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .clickable(onClick = onClick)
            .padding(vertical = 12.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Icon(
            painter = painterResource(icon),
            contentDescription = null,
            tint = MaterialTheme.colorScheme.primary,
        )
        Spacer(Modifier.width(16.dp))
        Column(modifier = Modifier.weight(1f)) {
            Text(
                text = title,
                style = MaterialTheme.typography.bodyLarge,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
            )
            Text(
                text = subtitle,
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
        Icon(
            painter = painterResource(R.drawable.ic_chevron_right),
            contentDescription = null,
            tint = MaterialTheme.colorScheme.onSurfaceVariant,
        )
    }
}
