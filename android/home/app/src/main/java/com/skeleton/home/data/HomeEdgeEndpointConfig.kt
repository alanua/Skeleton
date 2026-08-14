package com.skeleton.home.data

import android.content.Context
import android.content.pm.PackageManager
import android.net.Uri
import com.skeleton.home.R

data class HomeEdgeEndpointConfig(
    val baseUrl: String,
    val liveStatePath: String = "/api/operator/live-state",
) {
    val liveStateUrl: String
        get() = baseUrl.trimEnd('/') + liveStatePath

    init {
        require(baseUrl.startsWith("http://") || baseUrl.startsWith("https://")) {
            "operator_live_state_base_url_must_be_absolute"
        }
        require(Uri.parse(baseUrl).host?.isNotBlank() == true) {
            "operator_live_state_base_url_must_have_host"
        }
        require(liveStatePath == "/api/operator/live-state") {
            "operator_live_state_path_must_remain_canonical"
        }
    }

    companion object {
        private const val MetadataKey = "com.skeleton.home.OPERATOR_LIVE_STATE_BASE_URL"

        fun from(context: Context): HomeEdgeEndpointConfig {
            val packageManager = context.packageManager
            val appInfo = packageManager.getApplicationInfo(
                context.packageName,
                PackageManager.GET_META_DATA,
            )
            val metadataValue = appInfo.metaData?.get(MetadataKey)
            val baseUrl = when (metadataValue) {
                is String -> metadataValue
                is Int -> context.getString(metadataValue)
                else -> context.getString(R.string.operator_live_state_base_url)
            }
            return HomeEdgeEndpointConfig(baseUrl = baseUrl)
        }
    }
}
