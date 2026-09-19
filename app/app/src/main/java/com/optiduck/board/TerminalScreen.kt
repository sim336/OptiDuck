package com.optiduck.board

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import java.util.concurrent.TimeUnit

/** 远程终端：板子后台小黑框，黑底等宽，输入命令实时回显。 */
@Composable
fun TerminalScreen(mod: Modifier) {
    var output by remember { mutableStateOf("") }
    var input by remember { mutableStateOf("") }
    var status by remember { mutableStateOf("未连接") }   // 未连接 / 连接中 / 已连接(host)
    var connected by remember { mutableStateOf(false) }
    var token by remember { mutableStateOf("") }
    val scroll = rememberScrollState()

    val client by remember {
        mutableStateOf(OkHttpClient.Builder().pingInterval(20, TimeUnit.SECONDS).build())
    }
    var wsRef by remember { mutableStateOf<WebSocket?>(null) }

    fun push(text: String) {
        output = output + text
    }

    // 有新输出时自动滚到底部
    LaunchedEffect(output) {
        if (scroll.maxValue > 0) scroll.scrollTo(scroll.maxValue)
    }

    fun disconnect() {
        wsRef?.run { try { close(1000, "bye") } catch (_: Exception) {} }
        wsRef = null
        connected = false
        status = "已断开"
    }

    val listener = remember {
        object : WebSocketListener() {
            override fun onOpen(ws: WebSocket, response: Response) {
                wsRef = ws
                // 首包鉴权
                val t = token
                if (t.isNotEmpty()) ws.send(t)
            }
            override fun onMessage(ws: WebSocket, text: String) {
                connected = true
                status = "已连接"
                push(text)
            }
            override fun onClosed(ws: WebSocket, code: Int, reason: String) {
                connected = false
                status = "已断开($code)"
            }
            override fun onFailure(ws: WebSocket, t: Throwable, response: Response?) {
                connected = false
                status = "连接失败: ${t.message}"
            }
        }
    }

    // 用独立线程拉 token，取到后回调
    fun fetchTokenThen(go: () -> Unit) {
        TermClient.fetchToken(
            onOk = { t -> token = t; go() },
            onErr = { e -> status = e }
        )
    }

    fun connect() {
        status = "连接中…"
        fetchTokenThen {
            val req = Request.Builder().url(TermClient.wsUrl()).build()
            wsRef = client.newWebSocket(req, listener)
        }
    }

    DisposableEffect(Unit) {
        onDispose { wsRef?.run { try { close(1000, "page leave") } catch (_: Exception) {} } }
    }

    fun send() {
        val cmd = input.trimEnd()
        if (cmd.isEmpty() || !connected) return
        wsRef?.send(cmd + "\n")
        push("> $cmd\n")
        input = ""
    }

    Column(
        modifier = mod
            .fillMaxSize()
            .padding(12.dp)
    ) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text("远程终端", fontSize = 20.sp, color = Color(0xFFCDD6F4))
            Spacer(Modifier.width(10.dp))
            if (status.startsWith("连接") || status == "连接中…") {
                CircularProgressIndicator(modifier = Modifier.width(14.dp).height(14.dp), strokeWidth = 2.dp)
            }
            Text(status, fontSize = 12.sp, color = Color(0xFF89B4FA), modifier = Modifier.padding(start = 6.dp))
            Spacer(Modifier.weight(1f))
            OutlinedButton(onClick = { if (connected) disconnect() else connect() }) {
                Text(if (connected) "断开" else "连接", fontSize = 13.sp)
            }
        }

        Spacer(Modifier.height(8.dp))
        Text(
            modifier = Modifier
                .fillMaxWidth()
                .weight(1f)
                .background(Color.Black, RoundedCornerShape(6.dp))
                .verticalScroll(scroll)
                .padding(8.dp),
            text = output.ifEmpty { "（黑框终端，点“连接”以进入板子 shell）" },
            color = Color(0xFFD8FFC4),
            fontFamily = FontFamily.Monospace,
            fontSize = 13.sp,
            lineHeight = 16.sp
        )

        Spacer(Modifier.height(8.dp))
        Row(verticalAlignment = Alignment.CenterVertically) {
            OutlinedTextField(
                value = input,
                onValueChange = { input = it },
                modifier = Modifier.weight(1f),
                placeholder = { Text("输入命令回车执行", fontSize = 13.sp) },
                singleLine = true,
                textStyle = MaterialTheme.typography.bodyMedium.copy(fontFamily = FontFamily.Monospace)
            )
            Spacer(Modifier.width(8.dp))
            Button(onClick = { send() }, enabled = connected) {
                Text("执行")
            }
        }
    }
}