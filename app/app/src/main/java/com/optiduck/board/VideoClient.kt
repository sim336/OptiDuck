package com.optiduck.board

import java.net.URLEncoder

/** 画面页辅助：拼接 MJPEG 地址并从板子取 token。 */
object VideoClient {
    private const val VIDEO_PORT = 8072

    /** 板子地址（去掉协议前缀与端口）。 */
    fun host(): String {
        var h = Prefs.getHost()
        h = h.replace("http://", "").replace("https://", "")
        return if (h.contains(":")) h.substringBefore(":") else h
    }

    /** MJPEG 流地址：http://{host}:8072/video?token=... */
    fun streamUrl(token: String): String =
        "http://" + host() + ":" + VIDEO_PORT + "/video?token=" +
            URLEncoder.encode(token, "UTF-8")

    /** 板子 token（`/home/radxa/robot_terminal_token`）与终端服务是同一个，直接复用其取值路径。 */
    fun fetchToken(onOk: (String) -> Unit, onErr: (String) -> Unit) =
        TermClient.fetchToken(onOk, onErr)
}