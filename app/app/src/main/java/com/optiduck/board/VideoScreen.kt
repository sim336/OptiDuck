package com.optiduck.board

import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.os.Handler
import android.os.Looper
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import okhttp3.Call
import okhttp3.OkHttpClient
import okhttp3.Request
import java.io.ByteArrayOutputStream
import java.io.InputStream
import java.util.concurrent.TimeUnit

/** 实时画面：拉板子 8072 的 MJPEG 流，逐帧解码上屏。 */
@Composable
fun VideoScreen(mod: Modifier) {
    var bitmap by remember { mutableStateOf<Bitmap?>(null) }
    var status by remember { mutableStateOf("未连接") }   // 未连接 / 连接中… / 已连接 / 连接失败:…
    var live by remember { mutableStateOf(false) }        // 流是否正在推帧
    var frames by remember { mutableStateOf(0) }
    var fps by remember { mutableStateOf(0f) }
    var frameKb by remember { mutableStateOf(0) }
    var resolution by remember { mutableStateOf("—") }

    val main = remember { Handler(Looper.getMainLooper()) }
    val client by remember {
        mutableStateOf(
            OkHttpClient.Builder()
                .connectTimeout(5, TimeUnit.SECONDS)
                .readTimeout(0, TimeUnit.MILLISECONDS)   // MJPEG 是长连接，不能设读超时
                .build()
        )
    }
    var callRef by remember { mutableStateOf<Call?>(null) }

    fun stop(reason: String) {
        callRef?.cancel()
        callRef = null
        live = false
        status = reason
    }

    fun start() {
        status = "连接中…"
        VideoClient.fetchToken(
            onOk = { token ->
                val call = client.newCall(Request.Builder().url(VideoClient.streamUrl(token)).build())
                callRef = call
                Thread {
                    var count = 0
                    val t0 = System.currentTimeMillis()
                    try {
                        call.execute().use { resp ->
                            if (!resp.isSuccessful) {
                                val msg = resp.body?.string().orEmpty().take(120)
                                main.post { stop("连接失败: HTTP ${resp.code} $msg") }
                                return@Thread
                            }
                            main.post { live = true; status = "已连接" }
                            readJpegs(resp.body!!.byteStream()) { jpg ->
                                val bmp = BitmapFactory.decodeByteArray(jpg, 0, jpg.size)
                                if (bmp != null) {
                                    count++
                                    val n = count
                                    val kb = jpg.size / 1024
                                    val secs = (System.currentTimeMillis() - t0) / 1000f
                                    main.post {
                                        bitmap = bmp
                                        frames = n
                                        frameKb = kb
                                        resolution = "${bmp.width}×${bmp.height}"
                                        if (secs > 1f) fps = n / secs
                                    }
                                }
                            }
                            main.post { stop("已断开") }
                        }
                    } catch (e: Exception) {
                        main.post { stop("连接失败: ${e.message ?: e.javaClass.simpleName}") }
                    }
                }.start()
            },
            onErr = { e -> status = e }
        )
    }

    DisposableEffect(Unit) {
        onDispose { callRef?.cancel(); callRef = null }
    }

    Column(
        modifier = mod
            .fillMaxSize()
            .padding(12.dp)
    ) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text("实时画面", fontSize = 20.sp, color = Color(0xFFCDD6F4))
            Spacer(Modifier.width(10.dp))
            if (status == "连接中…") {
                CircularProgressIndicator(
                    modifier = Modifier.width(14.dp).height(14.dp),
                    strokeWidth = 2.dp
                )
            }
            Text(
                status,
                fontSize = 12.sp,
                color = if (live) Color(0xFF94E2D5) else Color(0xFF89B4FA),
                modifier = Modifier.padding(start = 6.dp)
            )
            Spacer(Modifier.weight(1f))
            OutlinedButton(onClick = { if (live || callRef != null) stop("已断开") else start() }) {
                Text(if (live) "断开" else "连接", fontSize = 13.sp)
            }
        }

        Spacer(Modifier.height(10.dp))
        Box(
            modifier = Modifier
                .fillMaxWidth()
                .aspectRatio(4f / 3f)
                .background(Color.Black, RoundedCornerShape(6.dp)),
            contentAlignment = Alignment.Center
        ) {
            val bmp = bitmap
            if (bmp != null) {
                Image(
                    bitmap = bmp.asImageBitmap(),
                    contentDescription = "实时画面",
                    modifier = Modifier.fillMaxSize(),
                    contentScale = ContentScale.Fit
                )
            } else {
                Text("（黑屏，点「连接」开始拉流）", fontSize = 13.sp, color = Color(0xFF6C7086))
            }
        }

        Spacer(Modifier.height(12.dp))
        StatRow("分辨率", resolution)
        StatRow("帧率", if (frames > 0) "%.1f fps".format(fps) else "—")
        StatRow("累计帧", if (frames > 0) frames.toString() else "—")
        StatRow("单帧", if (frameKb > 0) "$frameKb KB" else "—")

        Spacer(Modifier.height(10.dp))
        Text(
            "板端 report_video.py · MJPEG · 8072 端口 · 首包带 token（与终端服务共用同一 token）。",
            fontSize = 12.sp,
            color = Color(0xFF6C7086)
        )
        Text(
            "画面偏灰绿属正常：3A（AE/AWB）由 mediad 拉起，本机未启用，这里直接抓的是无校正的 ISP 输出。",
            fontSize = 12.sp,
            color = Color(0xFF6C7086),
            modifier = Modifier.padding(top = 4.dp)
        )
    }
}

@Composable
private fun StatRow(key: String, value: String) {
    Row(
        Modifier.fillMaxWidth().padding(vertical = 3.dp),
        horizontalArrangement = Arrangement.SpaceBetween
    ) {
        Text(key, fontSize = 13.sp, color = Color(0xFFA6ADC8))
        Text(value, fontSize = 13.sp, color = Color(0xFFCDD6F4))
    }
}

/**
 * 从 MJPEG 流里按 SOI(FFD8)/EOI(FFD9) 切出一张张 JPEG。
 *
 * 不必解析 multipart 边界：JPEG 熵编码段里 FF 后面必定跟 00 或另一个标记字节，
 * 所以 FFD9 在帧内不会误现，按标记切分是安全的。
 */
private fun readJpegs(input: InputStream, onJpeg: (ByteArray) -> Unit) {
    val buf = ByteArrayOutputStream(64 * 1024)
    val chunk = ByteArray(16 * 1024)
    var prev = -1
    var inFrame = false
    while (true) {
        val n = input.read(chunk)
        if (n < 0) break
        for (i in 0 until n) {
            val b = chunk[i].toInt() and 0xFF
            if (prev == 0xFF && b == 0xD8) {          // SOI：新帧开始
                buf.reset()
                buf.write(0xFF)
                buf.write(0xD8)
                inFrame = true
            } else if (inFrame) {
                buf.write(b)
                if (prev == 0xFF && b == 0xD9) {      // EOI：一张图收齐
                    onJpeg(buf.toByteArray())
                    buf.reset()
                    inFrame = false
                }
            }
            prev = b
        }
        if (buf.size() > 4 * 1024 * 1024) {           // 脏数据兜底，别让缓冲无限涨
            buf.reset()
            inFrame = false
        }
    }
}