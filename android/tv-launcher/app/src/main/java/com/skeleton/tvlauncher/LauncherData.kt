package com.skeleton.tvlauncher

import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL

data class CatalogItem(val id:String,val title:String,val detail:String,val art:String="")
object LauncherData {
    const val HISTORY="/api/video/history"
    const val CHANNELS="/api/tv/channels"
    fun history(json:String):List<CatalogItem>{
        val a=JSONObject(json).optJSONArray("items")?:return emptyList()
        return (0 until a.length()).mapNotNull{n->
            val v=a.optJSONObject(n)?:return@mapNotNull null
            val id=v.optString("history_id");val name=v.optString("title")
            if(v.optBoolean("hidden")||id.isBlank()||name.isBlank())null
            else CatalogItem(id,name,v.optString("runtime_label"),v.optString("poster"))
        }.take(150)
    }
    fun channels(json:String):List<CatalogItem>{
        val a=JSONObject(json).optJSONArray("channels")?:return emptyList()
        return (0 until a.length()).mapNotNull{n->
            val v=a.optJSONObject(n)?:return@mapNotNull null
            val id=v.optString("channel_id")
            val name=v.optString("display_name").ifBlank{v.optString("name")}
            if(id.isBlank()||name.isBlank())null
            else CatalogItem(id,name,v.optString("programme_title"),v.optString("picon"))
        }.take(300)
    }
    fun read(path:String):String{
        require(path==HISTORY||path==CHANNELS)
        val u=URL(BuildConfig.HOME_EDGE+path)
        require(u.host=="192.168.1.54")
        val c=u.openConnection() as HttpURLConnection
        try{
            c.connectTimeout=3000;c.readTimeout=4000
            c.requestMethod="GET"
            require(c.responseCode==200){"HTTP "+c.responseCode}
            return c.inputStream.bufferedReader().use{it.readText()}
        }finally{c.disconnect()}
    }
}
