package com.optiduck.board

object Prefs {
    const val NAME = "board_status"
    const val KEY_HOST = "host"
    const val DEFAULT_HOST = "172.16.0.127"

    private val prefs by lazy {
        App.instance.getSharedPreferences(NAME, android.content.Context.MODE_PRIVATE)
    }

    fun getHost(): String = prefs.getString(KEY_HOST, DEFAULT_HOST) ?: DEFAULT_HOST

    fun setHost(host: String) {
        prefs.edit().putString(KEY_HOST, host).apply()
    }

    fun url(): String {
        var h = getHost()
        if (h.startsWith("http://")) h = h.removePrefix("http://")
        if (h.startsWith("https://")) h = h.removePrefix("https://")
        return "http://$h/"
    }

    /** 状态服务地址（带默认端口 8070，除非用户在地址里已带端口） */
    fun statusBase(): String {
        var h = getHost()
        if (h.startsWith("http://")) h = h.removePrefix("http://")
        if (h.startsWith("https://")) h = h.removePrefix("https://")
        if (h.contains(":")) return "http://$h"
        return "http://$h:8070"
    }
}