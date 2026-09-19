package com.optiduck.board

import android.content.Context
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

/** 连接页：蒲公英检测/唤起 + 板子地址设置。 */
@Composable
fun ConnectScreen(mod: Modifier) {
    val ctx = LocalContext.current
    var pgyInfo by remember { mutableStateOf<Pair<String, String>?>(null) }
    var host by remember { mutableStateOf(Prefs.getHost()) }

    LaunchedEffect(Unit) {
        pgyInfo = PgyHelper.installed(ctx)
    }

    Column(
        modifier = mod
            .fillMaxSize()
            .verticalScroll(rememberScrollState())
            .padding(16.dp)
    ) {
        Text("连接板子", fontSize = 26.sp, color = Color(0xFFCDD6F4))
        Spacer(Modifier.height(4.dp))
        Text("内网穿透：蒲公英虚拟网", fontSize = 13.sp, color = Color(0xFF89B4FA))

        Spacer(Modifier.height(24.dp))
        Text("蒲公英", fontSize = 13.sp, color = Color(0xFFA6ADC8))

        Card(
            modifier = Modifier.fillMaxWidth().padding(top = 8.dp),
            colors = CardDefaults.cardColors(containerColor = Color(0xFF1E1E2E))
        ) {
            Column(Modifier.padding(16.dp)) {
                when {
                    pgyInfo != null -> Text(
                        "已检测到：${pgyInfo!!.second}（${pgyInfo!!.first}）。请在应用内登录贝锐账号并确认已连接。",
                        color = Color(0xFFCDD6F4), fontSize = 14.sp
                    )
                    pgyInfo == null -> Text(
                        "未检测到蒲公英 App（扫描到的应用里没有含 oray/蒲公英 的）。需先安装并登录后才能连板子虚拟 IP。",
                        color = Color(0xFFF38BA8), fontSize = 14.sp
                    )
                }
                Spacer(Modifier.height(12.dp))
                Button(
                    onClick = {
                        if (!PgyHelper.launch(ctx)) PgyHelper.openStore(ctx)
                    },
                    modifier = Modifier.fillMaxWidth()
                ) {
                    Text("唤起蒲公英")
                }
            }
        }

        Spacer(Modifier.height(24.dp))
        Text("板子地址", fontSize = 13.sp, color = Color(0xFFA6ADC8))
        OutlinedTextField(
            value = host,
            onValueChange = { host = it },
            modifier = Modifier.fillMaxWidth().padding(top = 8.dp),
            placeholder = { Text("如 172.16.0.127") },
            singleLine = true
        )
        Button(
            onClick = { Prefs.setHost(host.trim()) },
            modifier = Modifier.fillMaxWidth().padding(top = 8.dp)
        ) {
            Text("保存地址")
        }

        Spacer(Modifier.height(16.dp))
        Text("状态页将连接 " + Prefs.statusBase(), fontSize = 12.sp, color = Color(0xFF6C7086))

        Spacer(Modifier.height(24.dp))
        Text("检查更新", fontSize = 13.sp, color = Color(0xFFA6ADC8))
        UpdateCard(ctx)
    }
}

/** 在线更新卡片：检查版本、下载、安装。 */
@Composable
private fun UpdateCard(ctx: Context) {
    var info by remember { mutableStateOf<UpdateInfo?>(null) }
    var checking by remember { mutableStateOf(false) }
    var downloading by remember { mutableStateOf(false) }
    var err by remember { mutableStateOf<String?>(null) }
    val scope = rememberCoroutineScope()

    LaunchedEffect(Unit) {
        checking = true
        info = withContext(Dispatchers.IO) { Updater.check(ctx) }
        checking = false
    }

    Card(
        modifier = Modifier.fillMaxWidth().padding(top = 8.dp),
        colors = CardDefaults.cardColors(containerColor = Color(0xFF1E1E2E))
    ) {
        Column(Modifier.padding(16.dp)) {
            if (checking) {
                Row {
                    CircularProgressIndicator(
                        modifier = Modifier.height(20.dp),
                        strokeWidth = 2.dp,
                        color = Color(0xFF89B4FA)
                    )
                    Spacer(Modifier.width(10.dp))
                    Text("检查中…", color = Color(0xFFA6ADC8), fontSize = 14.sp)
                }
            } else {
                when {
                    info != null -> {
                        Text(
                            "发现新版本 v${info!!.versionName}（${fmtSize(info!!.size)}）",
                            color = Color(0xFF89B4FA), fontSize = 15.sp
                        )
                        if (info!!.changelog.isNotBlank()) {
                            Spacer(Modifier.height(6.dp))
                            Text(
                                "更新内容：${info!!.changelog}",
                                color = Color(0xFFA6ADC8), fontSize = 13.sp
                            )
                        }
                        Spacer(Modifier.height(10.dp))
                        Button(
                            onClick = {
                                scope.launch {
                                    downloading = true
                                    err = null
                                    runCatching {
                                        val f = withContext(Dispatchers.IO) { Updater.download(ctx, info!!) }
                                        Updater.install(ctx, f)
                                    }.onFailure { e -> err = e.message }
                                    downloading = false
                                }
                            },
                            enabled = !downloading,
                            modifier = Modifier.fillMaxWidth()
                        ) {
                            Text(if (downloading) "下载中…" else "下载并安装")
                        }
                        if (err != null) Text(err!!, color = Color(0xFFF38BA8), fontSize = 12.sp)
                    }
                    else -> {
                        Text("当前已是最新版本", color = Color(0xFF6C7086), fontSize = 14.sp)
                        OutlinedButton(
                            onClick = {
                                scope.launch {
                                    checking = true
                                    info = withContext(Dispatchers.IO) {
                                        Updater.check(ctx)
                                    }
                                    checking = false
                                }
                            },
                            modifier = Modifier.fillMaxWidth().padding(top = 8.dp)
                        ) { Text("重新检查", fontSize = 13.sp) }
                    }
                }
            }
        }
    }
}

private fun fmtSize(b: Long): String {
    val mb = b / (1024.0 * 1024.0)
    return "${"%.1f".format(mb)}MB"
}