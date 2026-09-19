package com.optiduck.board

import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL
import java.net.URLEncoder

/** 远程终端辅助：拼接 WebSocket 地址并从板子取 token。 */
object TermClient {
    private const val TERM_PORT = 8071

    /** 计算终端 WebSocket 地址：ws://{host}:8071/ */
    fun wsUrl(): String {
        var h = Prefs.getHost()
        h = h.replace("http://", "").replace("https://", "")
        val host = if (h.contains(":")) h.substringBefore(":") else h
        return "ws://$host:$TERM_PORT/"
    }

    /** 从板子状态服务拉取终端 token（异步回调）。 */
    fun fetchToken(onOk: (String) -> Unit, onErr: (String) -> Unit) {
        Thread {
            try {
                val url = URL(Prefs.statusBase() + "/api/term")
                val conn = url.openConnection() as HttpURLConnection
                conn.connectTimeout = 4000
                conn.readTimeout = 4000
                val body = conn.inputStream.bufferedReader().readText()
                conn.disconnect()
                val j = JSONObject(body)
                onOk(j.optString("token", ""))
            } catch (e: Exception) {
                onErr(e.message ?: "拉取 token 失败")
            }
        }.start()
    }

    /** 把 token 编码进地址（预留，当前用首包鉴权） */
    fun wsUrlWithToken(token: String): String =
        wsUrl().trimEnd('/') + "/?token=" + URLEncoder.encode(token, "UTF-8")
}