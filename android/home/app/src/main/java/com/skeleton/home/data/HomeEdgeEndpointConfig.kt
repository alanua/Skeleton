package com.skeleton.home.data

data class HomeEdgeEndpointConfig(
    val baseUrl: String = "http://192.168.1.54:8100",
) {
    fun operatorLiveStateUrl(): String = "${baseUrl.trimEnd('/')}/api/operator/live-state"
}
