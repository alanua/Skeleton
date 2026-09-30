package com.skeleton.home

import android.app.Activity
import android.Manifest
import android.app.DownloadManager
import android.app.PendingIntent
import android.content.pm.PackageInstaller
import android.content.pm.PackageManager
import android.content.Context
import android.content.Intent
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.graphics.Canvas as AndroidCanvas
import android.graphics.drawable.Drawable
import android.net.Uri
import android.os.Bundle
import android.os.Build
import android.widget.Toast
import android.hardware.ConsumerIrManager
import android.webkit.WebView
import android.webkit.WebViewClient
import android.webkit.JavascriptInterface
import android.webkit.WebChromeClient
import android.webkit.ConsoleMessage
import android.provider.Settings
import androidx.activity.ComponentActivity
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.activity.compose.setContent
import com.skeleton.home.health.HealthConnectAvailability
import com.skeleton.home.health.SkeletonHealthConnectAdapter
import com.skeleton.home.health.toSkeletonJson
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.basicMarquee
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.foundation.ExperimentalFoundationApi
import androidx.compose.foundation.clickable
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.foundation.gestures.detectHorizontalDragGestures
import androidx.compose.foundation.gestures.detectDragGesturesAfterLongPress
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.animation.core.LinearEasing
import androidx.compose.animation.core.tween
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.draw.shadow
import androidx.compose.ui.draw.scale
import androidx.compose.ui.draw.blur
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.platform.LocalSoftwareKeyboardController
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.toPixelMap
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.ImageBitmap
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.layout.Layout
import androidx.compose.ui.layout.onSizeChanged
import androidx.compose.ui.platform.LocalConfiguration
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.viewinterop.AndroidView
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.window.Dialog
import androidx.compose.ui.window.DialogProperties
import androidx.compose.ui.window.Popup
import androidx.compose.ui.window.PopupProperties
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.IntOffset
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.core.content.FileProvider
import androidx.core.content.ContextCompat
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import kotlinx.coroutines.withTimeoutOrNull
import org.json.JSONArray
import org.json.JSONObject
import java.security.MessageDigest
import java.io.BufferedReader
import java.io.File
import java.io.InputStreamReader
import java.net.HttpURLConnection
import java.net.URL
import java.net.ServerSocket
import java.net.InetAddress
import java.time.LocalDate
import java.util.Locale
import kotlin.math.min
import kotlin.math.abs

private val Bg = Color(0xFF0A0D10)
private val Card = Color(0xFF151A1F)
private val Tile = Color(0xFF12171C)
private val Action = Color(0xFF20272E)
private val Input = Color(0xFF11161A)
private val Line = Color(0xFF232A31)
private val Text = Color(0xFFF5F6F7)
private val Muted = Color(0xFF8D969F)
private val Accent = Color(0xFFFF6A00)
private val AccentSoft = Color(0xFF2A1C13)
private val Green = Color(0xFF58D57B)
private val Warn = Color(0xFFFFB454)
private val Purple = Color(0xFFB49CFF)

private val SkeletonScheme = darkColorScheme(
    primary = Accent, onPrimary = Color.White, background = Bg, onBackground = Text,
    surface = Card, onSurface = Text, surfaceVariant = Tile, onSurfaceVariant = Muted,
    outline = Line, error = Color(0xFFFF6B6B)
)
private val SkeletonTypography = Typography(
    bodyLarge = androidx.compose.ui.text.TextStyle(fontFamily = FontFamily.SansSerif, fontSize = 14.sp, color = Text),
    bodyMedium = androidx.compose.ui.text.TextStyle(fontFamily = FontFamily.SansSerif, fontSize = 11.sp, color = Muted),
    titleLarge = androidx.compose.ui.text.TextStyle(fontFamily = FontFamily.SansSerif, fontSize = 20.sp, fontWeight = FontWeight.SemiBold, color = Text),
    titleMedium = androidx.compose.ui.text.TextStyle(fontFamily = FontFamily.SansSerif, fontSize = 15.sp, fontWeight = FontWeight.SemiBold, color = Text),
    labelSmall = androidx.compose.ui.text.TextStyle(fontFamily = FontFamily.SansSerif, fontSize = 9.sp, fontWeight = FontWeight.SemiBold)
)

class MainActivity : ComponentActivity() {
    private val sharedUrl = mutableStateOf<String?>(null)
    private var pendingInstallUri: Uri? = null
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        window.statusBarColor = android.graphics.Color.BLACK
        window.navigationBarColor = android.graphics.Color.rgb(10,13,16)
        captureShare(intent)
        setContent { MaterialTheme(colorScheme = SkeletonScheme, typography = SkeletonTypography) { HomeComposeApp(sharedUrl.value) { sharedUrl.value = null } } }
    }
    override fun onNewIntent(intent: Intent) { super.onNewIntent(intent); setIntent(intent); captureShare(intent) }
    override fun onResume() {
        super.onResume()
        if(Build.VERSION.SDK_INT >= Build.VERSION_CODES.O && packageManager.canRequestPackageInstalls()) {
            pendingInstallUri?.let { uri -> pendingInstallUri=null; installWithPackageInstaller(uri) }
        }
    }
    fun requestPackageUpdateInstall(uri:Uri) {
        if(Build.VERSION.SDK_INT >= Build.VERSION_CODES.O && !packageManager.canRequestPackageInstalls()) {
            pendingInstallUri=uri
            startActivity(Intent(Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES,Uri.parse("package:$packageName")))
            return
        }
        installWithPackageInstaller(uri)
    }
    private fun installWithPackageInstaller(uri:Uri) {
        try {
            val installer=packageManager.packageInstaller
            val params=PackageInstaller.SessionParams(PackageInstaller.SessionParams.MODE_FULL_INSTALL).apply {
                setAppPackageName(packageName)
                if(Build.VERSION.SDK_INT>=Build.VERSION_CODES.S) setRequireUserAction(PackageInstaller.SessionParams.USER_ACTION_REQUIRED)
            }
            val sessionId=installer.createSession(params)
            installer.openSession(sessionId).use { session ->
                contentResolver.openInputStream(uri)?.use { input ->
                    session.openWrite("Home-update.apk",0,-1).use { output ->
                        input.copyTo(output)
                        session.fsync(output)
                    }
                } ?: throw IllegalStateException("Не вдалося відкрити завантажений APK")
                val resultIntent=Intent(this,InstallStatusReceiver::class.java).setAction(InstallStatusReceiver.ACTION_INSTALL_STATUS)
                val flags=PendingIntent.FLAG_UPDATE_CURRENT or if(Build.VERSION.SDK_INT>=Build.VERSION_CODES.S)PendingIntent.FLAG_MUTABLE else 0
                val pending=PendingIntent.getBroadcast(this,sessionId,resultIntent,flags)
                session.commit(pending.intentSender)
            }
        } catch(e:Exception) {
            Toast.makeText(this,e.message?:"Не вдалося передати оновлення системному інсталятору",Toast.LENGTH_LONG).show()
        }
    }
    private fun captureShare(i: Intent?) {
        if (i?.action != Intent.ACTION_SEND) return
        val text = i.getStringExtra(Intent.EXTRA_TEXT) ?: return
        val m = Regex("https?://\\S+").find(text) ?: return
        sharedUrl.value = m.value.trimEnd('.', ',', ')', ']', '}')
    }
}

private class HomeApi {
    val servers = BuildConfig.HOME_EDGE_BASE_URLS
        .split(',')
        .map { it.trim().trimEnd('/') }
        .filter { it.startsWith("http://") || it.startsWith("https://") }
        .distinct()
    var server by mutableStateOf<String?>(null)
    suspend fun discover(): String? = withContext(Dispatchers.IO) {
        for (s in servers) try {
            val c = URL("$s/health").openConnection() as HttpURLConnection
            c.connectTimeout = 1800; c.readTimeout = 1800
            val ok = c.responseCode in 200..299; c.disconnect()
            if (ok) { server = s; return@withContext s }
        } catch (_: Exception) {}
        server = null; null
    }
    suspend fun json(method: String, path: String, body: JSONObject? = null): JSONObject = withContext(Dispatchers.IO) {
        val base = server ?: throw IllegalStateException("Home Edge недоступний")
        val c = URL(base + path).openConnection() as HttpURLConnection
        c.connectTimeout = 3500; c.readTimeout = 10000; c.requestMethod = method
        c.setRequestProperty("Accept", "application/json")
        if (body != null) {
            c.doOutput = true; c.setRequestProperty("Content-Type", "application/json")
            c.outputStream.use { it.write(body.toString().toByteArray(Charsets.UTF_8)) }
        }
        val code = c.responseCode
        val input = if (code >= 400) c.errorStream else c.inputStream
        val text = input?.bufferedReader(Charsets.UTF_8)?.use(BufferedReader::readText).orEmpty()
        c.disconnect()
        val out = if (text.isBlank()) JSONObject() else JSONObject(text)
        if (code >= 400) throw IllegalStateException(out.optString("error", "HTTP $code"))
        out
    }
    suspend fun get(path: String) = json("GET", path)
    suspend fun post(path: String, body: JSONObject = JSONObject()) = json("POST", path, body)
    suspend fun put(path: String, body: JSONObject = JSONObject()) = json("PUT", path, body)
    suspend fun delete(path: String) = json("DELETE", path)
    suspend fun uploadCamera(context:Context,uri:Uri):JSONObject=withContext(Dispatchers.IO){
        val base=server?:throw IllegalStateException("Home Edge недоступний")
        val boundary="SkeletonCamera${System.currentTimeMillis()}"
        val c=URL(base+"/api/native/mfp/camera").openConnection() as HttpURLConnection
        c.connectTimeout=5000;c.readTimeout=30000;c.requestMethod="POST";c.doOutput=true;c.setRequestProperty("Accept","application/json");c.setRequestProperty("Content-Type","multipart/form-data; boundary=$boundary")
        c.outputStream.buffered().use{out->
            fun text(v:String){out.write(v.toByteArray(Charsets.UTF_8))}
            text("--$boundary\r\nContent-Disposition: form-data; name=\"image\"; filename=\"camera.jpg\"\r\nContent-Type: image/jpeg\r\n\r\n")
            context.contentResolver.openInputStream(uri)?.use{it.copyTo(out)}?:throw IllegalStateException("Не вдалося прочитати фото")
            text("\r\n--$boundary--\r\n")
        }
        val code=c.responseCode;val input=if(code>=400)c.errorStream else c.inputStream;val text=input?.bufferedReader(Charsets.UTF_8)?.use(BufferedReader::readText).orEmpty();c.disconnect()
        val obj=if(text.isBlank())JSONObject() else JSONObject(text);if(code>=400)throw IllegalStateException(obj.optString("error","HTTP $code"));obj
    }
    suspend fun uploadSecretFile(context:Context,uri:Uri):JSONObject=withContext(Dispatchers.IO){
        val base=server?:throw IllegalStateException("Home Edge недоступний")
        val boundary="SkeletonSecret${System.currentTimeMillis()}"
        val c=URL(base+"/api/native/home-edge/secrets/file").openConnection() as HttpURLConnection
        c.connectTimeout=5000;c.readTimeout=20000;c.requestMethod="POST";c.doOutput=true;c.setRequestProperty("Accept","application/json");c.setRequestProperty("Content-Type","multipart/form-data; boundary=$boundary")
        c.outputStream.buffered().use{out->
            fun text(v:String){out.write(v.toByteArray(Charsets.UTF_8))}
            text("--$boundary\r\nContent-Disposition: form-data; name=\"secret\"; filename=\"secret.json\"\r\nContent-Type: application/octet-stream\r\n\r\n")
            context.contentResolver.openInputStream(uri)?.use{input->val buf=ByteArray(32*1024);var total=0;while(true){val n=input.read(buf);if(n<=0)break;total+=n;if(total>131072)throw IllegalStateException("Файл секрету завеликий");out.write(buf,0,n)}}?:throw IllegalStateException("Не вдалося прочитати файл")
            text("\r\n--$boundary--\r\n")
        }
        val code=c.responseCode;val input=if(code>=400)c.errorStream else c.inputStream;val text=input?.bufferedReader(Charsets.UTF_8)?.use(BufferedReader::readText).orEmpty();c.disconnect()
        val obj=if(text.isBlank())JSONObject() else JSONObject(text);if(code>=400)throw IllegalStateException(obj.optString("error","HTTP $code"));obj
    }
}

private data class HomeUpdateInfo(val versionCode:Int,val versionName:String,val sha256:String,val bytes:Long,val apkPath:String)
private fun installedVersionCode(context:Context):Int {
    val p=context.packageManager.getPackageInfo(context.packageName,0)
    return if(Build.VERSION.SDK_INT>=Build.VERSION_CODES.P)p.longVersionCode.toInt() else @Suppress("DEPRECATION") p.versionCode
}
private fun parseHomeUpdate(j:JSONObject)=HomeUpdateInfo(
    j.optInt("version_code",0),j.optString("version_name"),j.optString("sha256").lowercase(Locale.ROOT),j.optLong("bytes",0L),j.optString("apk_path")
)
private suspend fun fetchHomeUpdate(api:HomeApi):HomeUpdateInfo? {
    if(api.server==null)return null
    return runCatching{parseHomeUpdate(api.get("/api/native/app-update"))}.getOrNull()?.takeIf{it.versionCode>0&&it.versionName.isNotBlank()&&it.sha256.length==64&&it.apkPath.isNotBlank()}
}
private suspend fun downloadHomeUpdate(context:Context,api:HomeApi,info:HomeUpdateInfo,onProgress:suspend(Int)->Unit):Uri=withContext(Dispatchers.IO){
    val base=api.server?:throw IllegalStateException("Home Edge недоступний")
    val url=if(info.apkPath.startsWith("http://")||info.apkPath.startsWith("https://"))info.apkPath else base+info.apkPath
    val dm=context.getSystemService(Context.DOWNLOAD_SERVICE) as DownloadManager
    val request=DownloadManager.Request(Uri.parse(url))
        .setTitle("Home ${info.versionName}")
        .setDescription("Оновлення Skeleton Home")
        .setMimeType("application/vnd.android.package-archive")
        .setNotificationVisibility(DownloadManager.Request.VISIBILITY_VISIBLE_NOTIFY_COMPLETED)
    val id=dm.enqueue(request)
    var result:Uri?=null
    while(result==null){
        val q=DownloadManager.Query().setFilterById(id)
        dm.query(q).use{c->
            if(!c.moveToFirst())throw IllegalStateException("Android не знайшов завантаження")
            val status=c.getInt(c.getColumnIndexOrThrow(DownloadManager.COLUMN_STATUS))
            val total=c.getLong(c.getColumnIndexOrThrow(DownloadManager.COLUMN_TOTAL_SIZE_BYTES))
            val done=c.getLong(c.getColumnIndexOrThrow(DownloadManager.COLUMN_BYTES_DOWNLOADED_SO_FAR))
            if(total>0&&done>=0){val pct=((done*100L)/total).coerceIn(0,100);withContext(Dispatchers.Main){onProgress(pct.toInt())}}
            when(status){
                DownloadManager.STATUS_SUCCESSFUL->{
                    val uri=dm.getUriForDownloadedFile(id)?:throw IllegalStateException("Android не повернув APK")
                    val digest=MessageDigest.getInstance("SHA-256")
                    context.contentResolver.openInputStream(uri)?.use{input->val buf=ByteArray(64*1024);while(true){val n=input.read(buf);if(n<=0)break;digest.update(buf,0,n)}}?:throw IllegalStateException("Не вдалося прочитати APK")
                    val actual=digest.digest().joinToString(""){"%02x".format(it.toInt() and 0xff)}
                    if(actual!=info.sha256)throw IllegalStateException("SHA-256 оновлення не збігається")
                    result=uri
                }
                DownloadManager.STATUS_FAILED->{
                    val reason=c.getInt(c.getColumnIndexOrThrow(DownloadManager.COLUMN_REASON))
                    throw IllegalStateException("Завантаження не вдалося ($reason)")
                }
                else->Unit
            }
        }
        if(result==null)delay(350)
    }
    result!!
}

private fun mdi(name: String): Int = when(name) {
    "home" -> R.drawable.home
    "movie-open" -> R.drawable.movie_open
    "devices" -> R.drawable.devices
    "monitor-dashboard" -> R.drawable.monitor_dashboard
    "youtube" -> R.drawable.youtube
    "cast-connected" -> R.drawable.cast_connected
    "television" -> R.drawable.television
    "gamepad-variant" -> R.drawable.gamepad_variant
    "television-play" -> R.drawable.television_play
    "keyboard-return" -> R.drawable.keyboard_return
    "chevron-up" -> R.drawable.chevron_up
    "chevron-left" -> R.drawable.chevron_left
    "chevron-right" -> R.drawable.chevron_right
    "chevron-down" -> R.drawable.chevron_down
    "volume-off" -> R.drawable.volume_off
    "rewind-15" -> R.drawable.rewind_15
    "play" -> R.drawable.play
    "pause" -> R.drawable.pause
    "fast-forward-15" -> R.drawable.fast_forward_15
    "volume-high" -> R.drawable.volume_high
    "lightbulb-outline" -> R.drawable.lightbulb_outline
    "lightbulb-on-outline" -> R.drawable.lightbulb_on_outline
    "dots-vertical" -> R.drawable.dots_vertical
    "history" -> R.drawable.history
    "magnify" -> R.drawable.magnify
    "close-circle-outline" -> R.drawable.close_circle_outline
    "playlist-play" -> R.drawable.playlist_play
    "quality-high" -> R.drawable.quality_high
    "account-voice" -> R.drawable.account_voice
    "subtitles" -> R.drawable.subtitles
    "thumb-up-outline" -> R.drawable.thumb_up_outline
    "refresh" -> R.drawable.refresh
    "autoplay-repeat" -> R.drawable.autoplay_repeat
    "radar" -> R.drawable.radar
    else -> R.drawable.information_outline
}

@Composable private fun Mdi(name:String, size:Dp=22.dp, tint:Color=Text, modifier:Modifier=Modifier) {
    Icon(painterResource(mdi(name)), contentDescription=null, tint=tint, modifier=modifier.size(size))
}

private val remoteBitmapCache = object: android.util.LruCache<String, ImageBitmap>(48 * 1024) {
    override fun sizeOf(key:String,value:ImageBitmap):Int=((value.width.toLong()*value.height.toLong()*4L)/1024L).coerceAtLeast(1L).coerceAtMost(Int.MAX_VALUE.toLong()).toInt()
}
private val remoteBitmapLocks = java.util.concurrent.ConcurrentHashMap<String,Any>()
private fun remoteBitmapKey(url:String,maxWidth:Int,maxHeight:Int)="$url@$maxWidth×$maxHeight"
private fun remoteBitmapDiskFile(context:Context,url:String):java.io.File {
    val digest=MessageDigest.getInstance("SHA-256").digest(url.toByteArray()).joinToString(""){"%02x".format(it)}
    val dir=java.io.File(context.cacheDir,"home-poster-cache-v1").apply{mkdirs()}
    return java.io.File(dir,"$digest.img")
}
private fun decodeRemoteBitmap(bytes:ByteArray,maxWidth:Int,maxHeight:Int):ImageBitmap? {
    if(bytes.isEmpty())return null
    val bounds=BitmapFactory.Options().apply{inJustDecodeBounds=true}
    BitmapFactory.decodeByteArray(bytes,0,bytes.size,bounds)
    var sample=1
    if(maxWidth>0&&maxHeight>0&&bounds.outWidth>0&&bounds.outHeight>0){
        while(bounds.outWidth/sample>maxWidth*2 || bounds.outHeight/sample>maxHeight*2) sample*=2
    }
    val opts=BitmapFactory.Options().apply{inSampleSize=sample.coerceAtLeast(1);inPreferredConfig=Bitmap.Config.ARGB_8888}
    return BitmapFactory.decodeByteArray(bytes,0,bytes.size,opts)?.asImageBitmap()
}
private fun loadRemoteBitmap(context:Context,url:String,maxWidth:Int,maxHeight:Int):ImageBitmap? {
    val lock=remoteBitmapLocks.computeIfAbsent(url){Any()}
    return synchronized(lock){
        try{
            val file=remoteBitmapDiskFile(context,url)
            var bytes=if(file.isFile&&file.length()>0)file.readBytes() else ByteArray(0)
            if(bytes.isEmpty()){
                bytes=(URL(url).openConnection() as HttpURLConnection).run{
                    connectTimeout=3000;readTimeout=5000;useCaches=true
                    setRequestProperty("Accept","image/avif,image/webp,image/*,*/*;q=0.8")
                    inputStream.use{it.readBytes()}
                }
                if(bytes.isNotEmpty()){
                    val tmp=java.io.File(file.parentFile,file.name+".tmp-"+System.nanoTime())
                    tmp.writeBytes(bytes)
                    if(!tmp.renameTo(file)){file.writeBytes(bytes);tmp.delete()}
                }
            }
            decodeRemoteBitmap(bytes,maxWidth,maxHeight)
        }catch(_:Exception){null}
        finally{remoteBitmapLocks.remove(url,lock)}
    }
}
@Composable private fun remoteBitmap(url:String?,maxWidth:Int=0,maxHeight:Int=0): ImageBitmap? {
    val context=LocalContext.current.applicationContext
    val key=if(url.isNullOrBlank())"" else remoteBitmapKey(url,maxWidth,maxHeight)
    val initial=remember(key){if(key.isBlank())null else remoteBitmapCache.get(key)}
    return produceState<ImageBitmap?>(initial,key){
        if(url.isNullOrBlank()||key.isBlank()){value=null;return@produceState}
        remoteBitmapCache.get(key)?.let{value=it;return@produceState}
        val loaded=withContext(Dispatchers.IO){loadRemoteBitmap(context,url,maxWidth,maxHeight)}
        if(loaded!=null)remoteBitmapCache.put(key,loaded)
        value=loaded
    }.value
}

private fun drawableBitmap(d:Drawable): ImageBitmap {
    val w = maxOf(1, d.intrinsicWidth); val h = maxOf(1, d.intrinsicHeight)
    val b = Bitmap.createBitmap(w,h,Bitmap.Config.ARGB_8888); val c=AndroidCanvas(b); d.setBounds(0,0,w,h); d.draw(c); return b.asImageBitmap()
}

private fun ambientComplement(bitmap:ImageBitmap?):Color{
    if(bitmap==null)return Color(0xFF9AA3AC)
    return runCatching{
        val pm=bitmap.toPixelMap();var rr=0f;var gg=0f;var bb=0f;var n=0
        val sx=maxOf(1,pm.width/12);val sy=maxOf(1,pm.height/12)
        var y=sy/2;while(y<pm.height){var x=sx/2;while(x<pm.width){val c=pm[x,y];rr+=c.red;gg+=c.green;bb+=c.blue;n++;x+=sx};y+=sy}
        val hsv=FloatArray(3);android.graphics.Color.RGBToHSV((rr/n*255).toInt(),(gg/n*255).toInt(),(bb/n*255).toInt(),hsv)
        hsv[0]=(hsv[0]+180f)%360f;hsv[1]=hsv[1].coerceIn(.45f,.82f);hsv[2]=.96f
        Color(android.graphics.Color.HSVToColor(hsv))
    }.getOrDefault(Color(0xFFE8EDF2))
}

@Composable private fun AmbientPosterBackground(bitmap:ImageBitmap?, modifier:Modifier=Modifier){
    Box(modifier.background(Bg)){
        if(bitmap!=null){
            // Full-screen overscan prevents a visible horizontal cutoff below the poster.
            Image(bitmap,null,Modifier.matchParentSize().scale(1.16f).blur(34.dp).alpha(.82f),contentScale=ContentScale.Crop)
            Box(Modifier.matchParentSize().background(Brush.verticalGradient(listOf(
                Color(0x12080B0E), Color(0x28080B0E), Color(0x58080B0E),
                Color(0xA6080B0E), Color(0xE8080B0E)
            ))))
        }
    }
}

private fun fmt(sec:Double):String { var t=sec.toInt().coerceAtLeast(0); val h=t/3600;t%=3600;val m=t/60;val s=t%60;return if(h>0)"$h:${m.toString().padStart(2,'0')}:${s.toString().padStart(2,'0')}" else "$m:${s.toString().padStart(2,'0')}" }
private fun JSONArray.objects(): List<JSONObject> = (0 until length()).mapNotNull { optJSONObject(it) }
private fun JSONArray.strings(): List<String> = (0 until length()).mapNotNull { optString(it).trim().takeIf(String::isNotBlank) }
private fun JSONObject.cleanText(key:String, fallback:String=""):String { val v=opt(key); if(v==null || v==JSONObject.NULL) return fallback; val out=v.toString().trim(); return if(out.isBlank() || out.equals("null",true)) fallback else out }
private fun String.ellipsize(n:Int=90)=if(length<=n)this else take(n-1)+"…"


private data class FamilyRecoveryAction(
    val id:String,
    val label:String,
    val url:String,
)
private data class FamilySecurityDevice(
    val id:String,
    val name:String,
    val role:String,
    val platform:String,
    val status:String,
    val lostAt:Long,
    val providers:List<String>,
    val recoveryActions:List<FamilyRecoveryAction>,
)
private fun parseFamilySecurityDevice(j:JSONObject)=FamilySecurityDevice(
    id=j.cleanText("device_id"),
    name=j.cleanText("name",j.cleanText("device_id")),
    role=j.cleanText("role"),
    platform=j.cleanText("platform"),
    status=j.cleanText("status","normal"),
    lostAt=j.optLong("lost_at",0L),
    providers=j.optJSONArray("recovery_providers")?.strings().orEmpty(),
    recoveryActions=j.optJSONArray("recovery_actions")?.objects().orEmpty().map{
        FamilyRecoveryAction(
            id=it.cleanText("id"),
            label=it.cleanText("label"),
            url=it.cleanText("url"),
        )
    }.filter{it.label.isNotBlank()&&it.url.startsWith("https://")},
)
private fun familySecurityRoleLabel(role:String)=when(role){
    "android_phone","ios_phone"->"Телефон"
    "tablet_kiosk"->"Планшет"
    else->"Пристрій"
}
private fun recoveryProviderLabel(id:String)=when(id){
    "google_find_hub"->"Google Find Hub"
    "xiaomi_find_device"->"Xiaomi Find Device"
    "apple_find_my"->"Apple Find My"
    "samsung_find"->"Samsung Find"
    else->id
}

@Composable
private fun FamilySecurityDialog(api:HomeApi,isFullProfile:Boolean,onDismiss:()->Unit){
    val scope=rememberCoroutineScope()
    val context=LocalContext.current
    var devices by remember{mutableStateOf<List<FamilySecurityDevice>>(emptyList())}
    var loaded by remember{mutableStateOf(false)}
    var busyId by remember{mutableStateOf("")}
    var confirmLost by remember{mutableStateOf<FamilySecurityDevice?>(null)}
    var statusText by remember{mutableStateOf("")}
    var canRecover by remember{mutableStateOf(isFullProfile)}

    fun refresh(){
        scope.launch{
            runCatching{api.get("/api/native/security/family-devices")}.onSuccess{j->
                devices=j.optJSONArray("items")?.objects().orEmpty().map(::parseFamilySecurityDevice)
                canRecover=j.optBoolean("can_recover",isFullProfile)
                loaded=true
            }.onFailure{
                loaded=true
                statusText=it.message?:"Не вдалося отримати сімейні пристрої"
            }
        }
    }
    LaunchedEffect(api.server){if(api.server!=null)refresh()}

    Dialog(
        onDismissRequest=onDismiss,
        properties=DialogProperties(usePlatformDefaultWidth=false)
    ){
        Column(Modifier.fillMaxSize().background(Bg).statusBarsPadding()){
            Row(
                Modifier.fillMaxWidth().padding(horizontal=14.dp,vertical=10.dp),
                verticalAlignment=Alignment.CenterVertically
            ){
                IconButton(onClick=onDismiss){Mdi("chevron-left",24.dp,Text)}
                Column(Modifier.weight(1f).padding(start=4.dp)){
                    Text("Безпека сім’ї",fontSize=20.sp,fontWeight=FontWeight.Bold,color=Text)
                    Text("Втрачені телефони та планшети",fontSize=10.sp,color=Muted,modifier=Modifier.padding(top=2.dp))
                }
                Mdi("radar",24.dp,Accent)
            }

            if(!loaded){
                Box(Modifier.fillMaxSize(),contentAlignment=Alignment.Center){
                    CircularProgressIndicator(color=Accent,strokeWidth=2.dp,modifier=Modifier.size(28.dp))
                }
            }else{
                LazyColumn(
                    Modifier.weight(1f).padding(horizontal=13.dp),
                    contentPadding=PaddingValues(bottom=28.dp),
                    verticalArrangement=Arrangement.spacedBy(10.dp)
                ){
                    item{
                        Column(Modifier.fillMaxWidth().clip(RoundedCornerShape(15.dp)).background(Card).border(1.dp,Line,RoundedCornerShape(15.dp)).padding(14.dp)){
                            Text("Аварійний режим",fontSize=14.sp,fontWeight=FontWeight.Bold,color=Text)
                            Text(
                                "«Телефон втрачено» лише вмикає LOST MODE. Дані не стираються. Повне стирання буде окремою операторською дією з повторним підтвердженням.",
                                fontSize=10.sp,lineHeight=15.sp,color=Muted,modifier=Modifier.padding(top=6.dp)
                            )
                        }
                    }
                    items(devices,key={it.id}){d->
                        val lost=d.status=="lost"
                        Column(
                            Modifier.fillMaxWidth().clip(RoundedCornerShape(16.dp))
                                .background(if(lost)Color(0xFF241719) else Card)
                                .border(1.dp,if(lost)Color(0xFF6A3138) else Line,RoundedCornerShape(16.dp))
                                .padding(14.dp)
                        ){
                            Row(Modifier.fillMaxWidth(),verticalAlignment=Alignment.CenterVertically){
                                Box(
                                    Modifier.size(38.dp).clip(RoundedCornerShape(11.dp))
                                        .background(if(lost)Color(0xFF4B2228) else Action),
                                    contentAlignment=Alignment.Center
                                ){
                                    Mdi(if(d.role=="tablet_kiosk")"devices" else "radar",20.dp,if(lost)Color(0xFFFF777F) else Accent)
                                }
                                Column(Modifier.weight(1f).padding(start=11.dp)){
                                    Text(d.name,fontSize=14.sp,fontWeight=FontWeight.SemiBold,color=Text,maxLines=2,overflow=TextOverflow.Ellipsis)
                                    Text(
                                        familySecurityRoleLabel(d.role)+" · "+if(lost)"LOST MODE" else "Нормально",
                                        fontSize=10.sp,color=if(lost)Color(0xFFFF8C93) else Green,modifier=Modifier.padding(top=3.dp)
                                    )
                                }
                            }
                            if(d.providers.isNotEmpty()){
                                Text(
                                    "Пошук/блокування: "+d.providers.joinToString(" · "){recoveryProviderLabel(it)},
                                    fontSize=9.sp,lineHeight=13.sp,color=Muted,modifier=Modifier.padding(top=9.dp)
                                )
                            }
                            if(d.recoveryActions.isNotEmpty()){
                                Column(
                                    Modifier.fillMaxWidth().padding(top=8.dp),
                                    verticalArrangement=Arrangement.spacedBy(6.dp)
                                ){
                                    d.recoveryActions.forEach{action->
                                        OutlinedButton(
                                            onClick={
                                                runCatching{
                                                    context.startActivity(
                                                        Intent(Intent.ACTION_VIEW,Uri.parse(action.url)).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                                                    )
                                                }.onFailure{
                                                    Toast.makeText(context,"Не вдалося відкрити "+action.label,Toast.LENGTH_LONG).show()
                                                }
                                            },
                                            modifier=Modifier.fillMaxWidth().height(42.dp),
                                            shape=RoundedCornerShape(11.dp),
                                            border=BorderStroke(1.dp,Line),
                                            colors=ButtonDefaults.outlinedButtonColors(contentColor=Text)
                                        ){
                                            Mdi("open-in-new",16.dp,Accent)
                                            Spacer(Modifier.width(7.dp))
                                            Text(action.label,fontSize=11.sp,fontWeight=FontWeight.SemiBold,maxLines=1,overflow=TextOverflow.Ellipsis)
                                        }
                                    }
                                }
                            }
                            if(!lost){
                                Button(
                                    onClick={confirmLost=d},
                                    enabled=busyId.isBlank(),
                                    modifier=Modifier.fillMaxWidth().height(46.dp).padding(top=10.dp),
                                    shape=RoundedCornerShape(12.dp),
                                    colors=ButtonDefaults.buttonColors(containerColor=Color(0xFF692E35))
                                ){
                                    Text(if(d.role=="tablet_kiosk")"Пристрій втрачено" else "Телефон втрачено",fontWeight=FontWeight.Bold,color=Color.White)
                                }
                            }else{
                                Text(
                                    "LOST MODE активний. Сам пристрій не може скасувати цей стан.",
                                    fontSize=10.sp,lineHeight=14.sp,color=Color(0xFFFFA1A6),modifier=Modifier.padding(top=9.dp)
                                )
                                if(canRecover){
                                    Button(
                                        onClick={
                                            busyId=d.id
                                            scope.launch{
                                                runCatching{api.post("/api/native/security/family-devices/"+d.id+"/recover",JSONObject())}
                                                    .onSuccess{statusText=d.name+": повернуто у NORMAL";refresh()}
                                                    .onFailure{statusText=it.message?:"Не вдалося скасувати LOST MODE"}
                                                busyId=""
                                            }
                                        },
                                        enabled=busyId.isBlank(),
                                        modifier=Modifier.fillMaxWidth().height(44.dp).padding(top=9.dp),
                                        shape=RoundedCornerShape(12.dp),
                                        colors=ButtonDefaults.buttonColors(containerColor=Action)
                                    ){Text("Пристрій знайдено",fontWeight=FontWeight.SemiBold,color=Text)}
                                }
                            }
                        }
                    }
                    if(statusText.isNotBlank())item{
                        Text(statusText,fontSize=11.sp,lineHeight=16.sp,color=Muted,modifier=Modifier.padding(horizontal=3.dp,vertical=4.dp))
                    }
                }
            }
        }

        val target=confirmLost
        if(target!=null){
            AlertDialog(
                onDismissRequest={if(busyId.isBlank())confirmLost=null},
                containerColor=Card,
                title={Text(if(target.role=="tablet_kiosk")"Пристрій втрачено?" else "Телефон втрачено?",color=Text,fontWeight=FontWeight.Bold)},
                text={
                    Column{
                        Text(target.name,color=Text,fontSize=14.sp,fontWeight=FontWeight.SemiBold)
                        Text(
                            "Буде ввімкнено LOST MODE. Це НЕ стирає дані. Стан зможе скасувати лише операторський профіль.",
                            color=Muted,fontSize=11.sp,lineHeight=16.sp,modifier=Modifier.padding(top=8.dp)
                        )
                    }
                },
                confirmButton={
                    Button(
                        onClick={
                            busyId=target.id
                            scope.launch{
                                runCatching{api.post("/api/native/security/family-devices/"+target.id+"/lost",JSONObject())}
                                    .onSuccess{statusText=target.name+": LOST MODE увімкнено";confirmLost=null;refresh()}
                                    .onFailure{statusText=it.message?:"Не вдалося ввімкнути LOST MODE"}
                                busyId=""
                            }
                        },
                        enabled=busyId.isBlank(),
                        colors=ButtonDefaults.buttonColors(containerColor=Color(0xFF7A3038))
                    ){Text("Увімкнути LOST MODE",color=Color.White,fontWeight=FontWeight.Bold)}
                },
                dismissButton={TextButton(onClick={confirmLost=null},enabled=busyId.isBlank()){Text("Скасувати",color=Muted)}}
            )
        }
    }
}

private data class NavDef(val title:String,val icon:String)
private data class HomeClientProfile(val name:String,val hasSk:Boolean)
private val fullOperatorNav = listOf(NavDef("Головна","home"),NavDef("Відео","movie-open"),NavDef("Пристрої","devices"),NavDef("СК","monitor-dashboard"))
private val familyNav = listOf(NavDef("Головна","home"),NavDef("Відео","movie-open"),NavDef("Сканувати","scanner"))
private fun navFor(profile:HomeClientProfile)=(if(profile.hasSk)fullOperatorNav else familyNav).map{it.copy(title=ui(it.title))}
private fun parseHomeProfile(j:JSONObject)=HomeClientProfile(j.optString("profile","family"),j.optBoolean("has_sk",false))

/** Keep every tab composed so remember/LazyListState/API-loaded UI state survives tab changes.
 * Inactive tabs are measured but deliberately not placed, so they cannot draw or receive input.
 */
@Composable
private fun PersistentTabSlot(active:Boolean, content:@Composable ()->Unit){
    Layout(
        modifier=Modifier.fillMaxSize(),
        content=content
    ){measurables,constraints->
        val placeables=measurables.map{it.measure(constraints)}
        val width=if(constraints.hasBoundedWidth)constraints.maxWidth else placeables.maxOfOrNull{it.width}?:0
        val height=if(constraints.hasBoundedHeight)constraints.maxHeight else placeables.maxOfOrNull{it.height}?:0
        layout(width,height){
            if(active)placeables.forEach{it.placeRelative(0,0)}
        }
    }
}

@Composable
private fun HomeComposeApp(sharedUrl:String?, consumeShare:()->Unit) {
    val api = remember { HomeApi() }
    val scope = rememberCoroutineScope()
    val context=LocalContext.current
    val uiPrefs=remember(context){context.getSharedPreferences("home_ui",Context.MODE_PRIVATE)}
    var uiLanguage by rememberSaveable { mutableStateOf(uiPrefs.getString("interface_language","uk") ?: "uk") }
    activeUiLanguage=uiLanguage
    var contentLanguage by rememberSaveable { mutableStateOf("uk") }
    var languageDialog by remember { mutableStateOf("") }
    var tab by rememberSaveable { mutableIntStateOf(0) }
    var hyperion by remember { mutableStateOf(false) }
    var outputTarget by rememberSaveable { mutableStateOf("tv") }
    var menu by remember { mutableStateOf(false) }
    var info by remember { mutableStateOf(false) }
    var historyEditor by remember { mutableStateOf(false) }
    var channelEditor by remember { mutableStateOf(false) }
    var deviceOrderEditor by remember { mutableStateOf(false) }
    var screensaver by remember { mutableStateOf(false) }
    var familySecurity by remember { mutableStateOf(false) }
    var updateInfo by remember { mutableStateOf<HomeUpdateInfo?>(null) }
    var updateBusy by remember { mutableStateOf(false) }
    var updateProgress by remember { mutableIntStateOf(0) }
    var discovered by remember { mutableStateOf(false) }
    var profile by remember { mutableStateOf<HomeClientProfile?>(null) }
    var videoSelectionRevision by rememberSaveable { mutableIntStateOf(0) }
    LaunchedEffect(Unit) {
        var lastUpdateCheckAt=0L
        var lastOfferedVersionCode=0
        while(true){
            api.discover()
            if(api.server!=null){
                runCatching{parseHomeProfile(api.get("/api/native/profile"))}.onSuccess{latest->
                    profile=latest
                    if(!latest.hasSk && tab>2) tab=0
                    discovered=true
                    runCatching{api.get("/api/preferences/language")}.onSuccess{lp->contentLanguage=lp.optString("content_language","uk").ifBlank{"uk"}}
                }
                runCatching { hyperion=api.get("/api/hyperion").optBoolean("enabled") }
                runCatching { outputTarget=api.get("/api/media/target").optString("target","tv").ifBlank{"tv"} }
                val now=System.currentTimeMillis()
                if(now-lastUpdateCheckAt>=60_000L){
                    fetchHomeUpdate(api)?.let { latest ->
                        if(latest.versionCode>installedVersionCode(context) && latest.versionCode!=lastOfferedVersionCode){
                            updateInfo=latest
                            updateProgress=0
                            lastOfferedVersionCode=latest.versionCode
                        }
                    }
                    lastUpdateCheckAt=now
                }
            } else if(profile==null && !discovered){
                // Unknown/offline clients get the safe two-tab family shell. A
                // previously confirmed full profile is never downgraded merely
                // because Wi-Fi/Tailscale disappeared for a moment.
                profile=HomeClientProfile("family",false)
                discovered=true
            }
            delay(if(api.server==null)1500 else 5000)
        }
    }
    LaunchedEffect(sharedUrl, api.server) {
        if(!sharedUrl.isNullOrBlank() && api.server!=null) {
            runCatching { api.post("/api/share",JSONObject().put("url",sharedUrl)) }.onSuccess { videoSelectionRevision++; tab=1; consumeShare() }
        }
    }
    Surface(Modifier.fillMaxSize(), color=Bg) {
        Box(Modifier.fillMaxSize()) {
        Column(Modifier.fillMaxSize().statusBarsPadding().then(if(updateInfo!=null) Modifier.blur(14.dp) else Modifier)) {
            Box(Modifier.weight(1f)) {
                val p=profile
                if(discovered && p!=null){
                    PersistentTabSlot(tab==0) { HomeScreen(api, tab==0, hyperion, { on -> hyperion=on; scope.launch { runCatching { api.post("/api/hyperion",JSONObject().put("enabled",on)) }.onFailure { hyperion=!on } } }, outputTarget, {target->scope.launch{runCatching{api.put("/api/media/target",JSONObject().put("target",target))}.onSuccess{outputTarget=it.optString("target",target)}}}, {menu=true}) }
                    PersistentTabSlot(tab==1) { VideoScreen(api, tab==1, videoSelectionRevision, hyperion, {on->hyperion=on;scope.launch{runCatching{api.post("/api/hyperion",JSONObject().put("enabled",on))}.onFailure{hyperion=!on}}},outputTarget,{target->scope.launch{runCatching{api.put("/api/media/target",JSONObject().put("target",target))}.onSuccess{outputTarget=it.optString("target",target)}}},{menu=true},{tab=0}) }
                    PersistentTabSlot(tab==2) {
                        if(p.hasSk) DevicesScreen(api, tab==2, hyperion, {on->hyperion=on;scope.launch{runCatching{api.post("/api/hyperion",JSONObject().put("enabled",on))}.onFailure{hyperion=!on}}},outputTarget,{target->scope.launch{runCatching{api.put("/api/media/target",JSONObject().put("target",target))}.onSuccess{outputTarget=it.optString("target",target)}}},{menu=true})
                        else FamilyScannerScreen(api, tab==2, hyperion, {on->hyperion=on;scope.launch{runCatching{api.post("/api/hyperion",JSONObject().put("enabled",on))}.onFailure{hyperion=!on}}},outputTarget,{target->scope.launch{runCatching{api.put("/api/media/target",JSONObject().put("target",target))}.onSuccess{outputTarget=it.optString("target",target)}}},{menu=true})
                    }
                    if(p.hasSk) PersistentTabSlot(tab==3) { SkeletonScreen(api, tab==3, hyperion, {on->hyperion=on;scope.launch{runCatching{api.post("/api/hyperion",JSONObject().put("enabled",on))}.onFailure{hyperion=!on}}},outputTarget,{target->scope.launch{runCatching{api.put("/api/media/target",JSONObject().put("target",target))}.onSuccess{outputTarget=it.optString("target",target)}}},{menu=true}) }
                } else Box(Modifier.fillMaxSize(), contentAlignment=Alignment.Center) { CircularProgressIndicator(color=Accent,strokeWidth=2.dp,modifier=Modifier.size(28.dp)) }
            }
            profile?.let { BottomNav(tab,navFor(it)) { tab=it } }
        }
        val pendingUpdate=updateInfo
        if(pendingUpdate!=null){
            HomeUpdateOverlay(
                info=pendingUpdate,
                busy=updateBusy,
                progress=updateProgress,
                onStart={
                    if(!updateBusy) scope.launch {
                        updateBusy=true;updateProgress=0
                        runCatching{downloadHomeUpdate(context,api,pendingUpdate){pct->updateProgress=pct}}.onSuccess{uri->
                            updateProgress=100
                            val activity=context as? MainActivity ?: throw IllegalStateException("Home Activity недоступна для системного оновлення")
                            activity.requestPackageUpdateInstall(uri)
                            updateInfo=null
                        }.onFailure{e->
                            updateBusy=false;updateProgress=0
                            Toast.makeText(context,e.message?:"Оновлення не вдалося",Toast.LENGTH_LONG).show()
                        }
                    }
                }
            )
        }
        }
    }
    if(menu){
        val density=LocalDensity.current
        Popup(
            alignment=Alignment.TopEnd,
            offset=IntOffset(with(density){(-10).dp.roundToPx()},with(density){72.dp.roundToPx()}),
            onDismissRequest={menu=false},
            properties=PopupProperties(focusable=true)
        ){
            Column(Modifier.widthIn(min=240.dp,max=320.dp).background(Color(0xFF171B20),RoundedCornerShape(16.dp)).border(1.dp,Color(0xFF31363C),RoundedCornerShape(16.dp)).padding(vertical=2.dp)){
                OverflowMenuItem("history",ui("Редагувати історію")) { menu=false; historyEditor=true }
                OverflowMenuItem("television",ui("Редагувати канали")) { menu=false; channelEditor=true }
                if(tab==2 && profile?.hasSk==true) OverflowMenuItem("sort",ui("Редагувати порядок пристроїв")) { menu=false; deviceOrderEditor=true }
                OverflowMenuItem("radar",ui("Безпека сім’ї")) { menu=false; familySecurity=true }
                OverflowMenuItem("monitor-dashboard",ui("Скрінсейвер")) { menu=false; screensaver=true }
                OverflowMenuItem("account-voice",ui("Мова інтерфейсу")+" · "+languageNativeLabel(uiLanguage)) { menu=false; languageDialog="interface" }
                OverflowMenuItem("subtitles",ui("Переклад")+" · "+languageNativeLabel(contentLanguage)) { menu=false; languageDialog="content" }
                OverflowMenuItem("refresh",ui("Оновити застосунок")) {
                    menu=false
                    scope.launch {
                        val latest=fetchHomeUpdate(api)
                        if(latest==null){Toast.makeText(context,"Не вдалося перевірити оновлення",Toast.LENGTH_LONG).show()}
                        else if(latest.versionCode<=installedVersionCode(context)){Toast.makeText(context,"У вас уже остання версія Home",Toast.LENGTH_SHORT).show()}
                        else {updateInfo=latest;updateProgress=0}
                    }
                }
                OverflowMenuItem("information-outline",ui("Інфо")) { menu=false; info=true }
            }
        }
    }
    if(familySecurity) FamilySecurityDialog(api, profile?.hasSk==true){familySecurity=false}
    if(languageDialog.isNotBlank()) LanguageChoiceDialog(
        title=ui(if(languageDialog=="interface")"Вибір мови інтерфейсу" else "Переклад"),
        selected=if(languageDialog=="interface")uiLanguage else contentLanguage,
        onDismiss={languageDialog=""},
        onSelect={code->
            if(languageDialog=="interface"){
                uiLanguage=code;activeUiLanguage=code;uiPrefs.edit().putString("interface_language",code).apply();languageDialog="";scope.launch{runCatching{api.put("/api/preferences/language",JSONObject().put("interface_language",code))}}
            } else {
                contentLanguage=code;languageDialog=""
                scope.launch{runCatching{api.put("/api/preferences/language",JSONObject().put("content_language",code))}}
            }
        }
    )

    if(deviceOrderEditor) DeviceOrderDialog(api,uiPrefs){deviceOrderEditor=false}
    if(historyEditor) HistoryEditorDialog(api){historyEditor=false}
    if(channelEditor) ChannelEditorDialog(api){channelEditor=false}
    if(screensaver) ScreensaverDialog(api){screensaver=false}
    if(info) HomeInfoDialog{info=false}
}


@Composable private fun HomeUpdateOverlay(info:HomeUpdateInfo,busy:Boolean,progress:Int,onStart:()->Unit){
    val ringSize=158.dp
    val pct=progress.coerceIn(0,100)
    Box(
        Modifier.fillMaxSize(),
        contentAlignment=Alignment.Center
    ){
        Box(
            Modifier.size(ringSize)
                .shadow(18.dp,CircleShape)
                .clip(CircleShape)
                .background(Color(0xE91A1F24))
                .clickable(enabled=!busy,onClick=onStart),
            contentAlignment=Alignment.Center
        ){
            Canvas(Modifier.matchParentSize().padding(5.dp)){
                val stroke=Stroke(width=9.dp.toPx())
                drawArc(color=Color(0xFF343B43),startAngle=-90f,sweepAngle=360f,useCenter=false,style=stroke)
                val sweep=if(busy)360f*pct/100f else 360f
                drawArc(
                    brush=Brush.sweepGradient(listOf(Color(0xFFFFA51F),Color(0xFFFF6A00),Color(0xFFFF3B4D),Color(0xFFFFA51F))),
                    startAngle=-90f,sweepAngle=sweep,useCenter=false,style=stroke
                )
            }
            if(busy){
                Text("$pct%",fontSize=31.sp,fontWeight=FontWeight.Medium,color=Text)
            }else{
                Column(horizontalAlignment=Alignment.CenterHorizontally){
                    Text(ui("Оновити"),fontSize=25.sp,fontWeight=FontWeight.SemiBold,color=Text)
                    Text(info.versionName,fontSize=10.sp,color=Muted,modifier=Modifier.padding(top=2.dp))
                }
            }
        }
    }
}

@Composable private fun HomeUpdateDialog(info:HomeUpdateInfo,busy:Boolean,status:String,onDismiss:()->Unit,onUpdate:()->Unit){
    Dialog(onDismissRequest=onDismiss,properties=DialogProperties(usePlatformDefaultWidth=false)){
        Box(Modifier.fillMaxSize().background(Color.Black.copy(alpha=.68f)).clickable(enabled=!busy,onClick=onDismiss),contentAlignment=Alignment.Center){
            Column(Modifier.fillMaxWidth(.86f).widthIn(max=350.dp).clip(RoundedCornerShape(22.dp)).background(Color(0xFF191D22)).border(1.dp,Color(0xFF383E45),RoundedCornerShape(22.dp)).clickable(enabled=false){}.padding(22.dp),horizontalAlignment=Alignment.CenterHorizontally){
                Mdi("refresh",42.dp,Accent)
                Text(ui("Доступне оновлення"),fontSize=19.sp,fontWeight=FontWeight.Bold,color=Text,modifier=Modifier.padding(top=12.dp))
                Text("Home ${info.versionName}",fontSize=14.sp,color=Color(0xFFE7EBEE),modifier=Modifier.padding(top=6.dp))
                Text(if(busy)status else "Завантажити, перевірити SHA-256 і відкрити системне встановлення?",fontSize=11.sp,lineHeight=16.sp,color=Muted,textAlign=androidx.compose.ui.text.style.TextAlign.Center,modifier=Modifier.padding(top=8.dp,bottom=16.dp))
                if(busy) CircularProgressIndicator(color=Accent,strokeWidth=2.dp,modifier=Modifier.size(28.dp))
                else Row(Modifier.fillMaxWidth(),horizontalArrangement=Arrangement.spacedBy(10.dp)){
                    OutlinedButton(onClick=onDismiss,modifier=Modifier.weight(1f).height(44.dp),shape=RoundedCornerShape(12.dp)){Text(ui("Пізніше"),color=Text)}
                    Button(onClick=onUpdate,modifier=Modifier.weight(1f).height(44.dp),shape=RoundedCornerShape(12.dp),colors=ButtonDefaults.buttonColors(containerColor=Accent)){Text(ui("Оновити"),color=Color.White,fontWeight=FontWeight.Bold)}
                }
            }
        }
    }
}

@Composable private fun LanguageChoiceDialog(title:String,selected:String,onDismiss:()->Unit,onSelect:(String)->Unit){
    Dialog(onDismissRequest=onDismiss,properties=DialogProperties(usePlatformDefaultWidth=false)){
        Box(Modifier.fillMaxSize().background(Color.Black.copy(alpha=.72f)).clickable(onClick=onDismiss),contentAlignment=Alignment.Center){
            Column(Modifier.fillMaxWidth(.86f).widthIn(max=360.dp).clip(RoundedCornerShape(20.dp)).background(Color(0xFF191D22)).border(1.dp,Color(0xFF383E45),RoundedCornerShape(20.dp)).clickable(enabled=false){}.padding(vertical=10.dp)){
                Text(title,fontSize=19.sp,fontWeight=FontWeight.Bold,color=Text,modifier=Modifier.padding(horizontal=20.dp,vertical=12.dp))
                HOME_LANGUAGES.forEach{lang->
                    Row(Modifier.fillMaxWidth().height(54.dp).clickable{onSelect(lang.code)}.padding(horizontal=20.dp),verticalAlignment=Alignment.CenterVertically){
                        Text(lang.nativeLabel,fontSize=16.sp,color=Text,modifier=Modifier.weight(1f))
                        if(lang.code==selected)Mdi("close-circle-outline",22.dp,Accent)
                    }
                }
            }
        }
    }
}

@Composable private fun OverflowMenuItem(icon:String,title:String,onClick:()->Unit){
    DropdownMenuItem(
        text={Text(title,color=Text,fontSize=14.sp,fontWeight=FontWeight.Normal)},
        leadingIcon={Mdi(icon,23.dp,Text)},
        onClick=onClick,
        modifier=Modifier.height(48.dp).clip(RoundedCornerShape(11.dp))
    )
}

@Composable private fun HomeInfoDialog(onDismiss:()->Unit){
    val context=LocalContext.current
    val version=remember(context){runCatching{context.packageManager.getPackageInfo(context.packageName,0).versionName}.getOrNull()?:"—"}
    Dialog(onDismissRequest=onDismiss,properties=DialogProperties(usePlatformDefaultWidth=false)){
        Box(Modifier.fillMaxSize().background(Color.Black.copy(alpha=.68f)).clickable(onClick=onDismiss),contentAlignment=Alignment.Center){
            Box(Modifier.fillMaxWidth(.84f).widthIn(max=330.dp).clip(RoundedCornerShape(24.dp)).background(Color(0xFF191D22)).border(1.dp,Color(0xFF383E45),RoundedCornerShape(24.dp)).clickable(enabled=false){}.padding(horizontal=28.dp,vertical=28.dp)){
                Text("×",fontSize=30.sp,lineHeight=30.sp,color=Text,modifier=Modifier.align(Alignment.TopEnd).offset(x=8.dp,y=(-8).dp).clickable(onClick=onDismiss))
                Column(Modifier.fillMaxWidth().padding(top=6.dp),horizontalAlignment=Alignment.CenterHorizontally){
                    Mdi("information-outline",44.dp,Accent)
                    Text("Home",fontSize=24.sp,fontWeight=FontWeight.Bold,color=Text,modifier=Modifier.padding(top=14.dp))
                    Text(ui("Версія застосунку $version"),fontSize=14.sp,color=Color(0xFFE7EBEE),modifier=Modifier.padding(top=6.dp,bottom=8.dp))
                    Text("Skeleton Home Edge",fontSize=11.sp,color=Muted)
                }
            }
        }
    }
}

@Composable private fun HistoryEditorDialog(api:HomeApi,onDismiss:()->Unit){
    val scope=rememberCoroutineScope();var items by remember{mutableStateOf(emptyList<JSONObject>())};var query by remember{mutableStateOf("")};var busy by remember{mutableStateOf(false)};val focusRequester=remember{FocusRequester()};val keyboard=LocalSoftwareKeyboardController.current
    suspend fun reload(){if(api.server==null)return;busy=true;items=runCatching{api.get("/api/video/history").optJSONArray("items")?.objects().orEmpty()}.getOrElse{emptyList()};busy=false}
    LaunchedEffect(api.server){reload()}
    val q=query.trim().lowercase(Locale.ROOT)
    val visible=if(q.isBlank())items else items.filter{ x -> listOf(x.optString("title"),x.optString("year"),x.optString("content_type"),x.optString("overview")).joinToString(" ").lowercase(Locale.ROOT).contains(q) }
    Dialog(onDismissRequest=onDismiss,properties=DialogProperties(usePlatformDefaultWidth=false)){
        Box(Modifier.fillMaxSize().background(Color.Black.copy(alpha=.72f)).clickable(onClick=onDismiss),contentAlignment=Alignment.BottomCenter){
            Column(Modifier.fillMaxWidth().widthIn(max=500.dp).fillMaxHeight(.92f).clip(RoundedCornerShape(topStart=18.dp,topEnd=18.dp)).background(Input).border(1.dp,Line,RoundedCornerShape(topStart=18.dp,topEnd=18.dp)).clickable(enabled=false){}){
                Row(Modifier.fillMaxWidth().height(52.dp).padding(horizontal=15.dp),verticalAlignment=Alignment.CenterVertically){
                    Text(ui("Редагування історії"),fontSize=17.sp,fontWeight=FontWeight.Bold,color=Text,modifier=Modifier.weight(1f))
                    Mdi("close-circle-outline",24.dp,Muted,Modifier.clickable(onClick=onDismiss))
                }
                Column(Modifier.fillMaxWidth().background(Input).border(width=0.dp,color=Color.Transparent).padding(horizontal=12.dp,vertical=10.dp)){
                    Row(Modifier.fillMaxWidth().height(42.dp).clip(RoundedCornerShape(12.dp)).background(Color(0xFF0B1014)).border(1.dp,Color(0xFF353C42),RoundedCornerShape(12.dp)).clickable{focusRequester.requestFocus();keyboard?.show()}.padding(horizontal=11.dp),verticalAlignment=Alignment.CenterVertically){
                        Mdi("magnify",20.dp,Muted);Spacer(Modifier.width(8.dp));
                        androidx.compose.foundation.text.BasicTextField(value=query,onValueChange={query=it},singleLine=true,textStyle=androidx.compose.ui.text.TextStyle(color=Text,fontSize=14.sp),modifier=Modifier.weight(1f).focusRequester(focusRequester),decorationBox={inner->Box(Modifier.fillMaxWidth()){if(query.isEmpty())Text(ui("Пошук в історії"),color=Color(0xFF747D85),fontSize=14.sp);inner()}})
                    }
                    Row(Modifier.fillMaxWidth().padding(horizontal=2.dp,vertical=6.dp),verticalAlignment=Alignment.CenterVertically){Text(ui("Останні додані або переглянуті — першими"),fontSize=10.sp,color=Muted);Spacer(Modifier.weight(1f));Text(if(q.isBlank())"${items.size}" else "${visible.size} з ${items.size}",fontSize=10.sp,fontWeight=FontWeight.Bold,color=Accent)}
                }
                if(busy) Box(Modifier.fillMaxSize(),contentAlignment=Alignment.Center){CircularProgressIndicator(color=Accent,strokeWidth=2.dp,modifier=Modifier.size(28.dp))}
                else LazyColumn(Modifier.weight(1f).fillMaxWidth().padding(horizontal=10.dp),contentPadding=PaddingValues(bottom=18.dp)){
                    items(visible,key={it.optString("history_id",it.optString("title"))}){item->HistoryEditorRow(api,item,{scope.launch{runCatching{api.delete("/api/video/history/${Uri.encode(item.optString("history_id"))}")};reload()} })}
                }
            }
        }
    }
}

@Composable private fun HistoryEditorRow(api:HomeApi,item:JSONObject,onDelete:()->Unit){
    val poster=item.optString("poster");val bm=remoteBitmap(if(poster.isBlank())null else if(poster.startsWith("http"))poster else api.server+poster,192,288)
    Row(Modifier.fillMaxWidth().heightIn(min=112.dp).border(width=0.dp,color=Color.Transparent).padding(horizontal=3.dp,vertical=9.dp),verticalAlignment=Alignment.CenterVertically){
        Box(Modifier.width(64.dp).height(96.dp).clip(RoundedCornerShape(8.dp)).background(Bg),contentAlignment=Alignment.Center){if(bm!=null)Image(bm,null,Modifier.fillMaxSize(),contentScale=ContentScale.Crop)else Mdi("movie-open",30.dp,Muted)}
        Column(Modifier.weight(1f).padding(start=11.dp,end=6.dp)){Text(item.optString("title","Твір"),fontSize=15.sp,lineHeight=19.sp,fontWeight=FontWeight.SemiBold,color=Text,maxLines=2,overflow=TextOverflow.Ellipsis);val meta=listOf(historyKindLabel(item),item.optString("year"),historyRuntimeLabel(item)).filter{it.isNotBlank()}.joinToString(" · ");if(meta.isNotBlank())Text(meta,fontSize=10.sp,fontWeight=FontWeight.Bold,color=Accent,modifier=Modifier.padding(top=4.dp));val ov=item.optString("overview");if(ov.isNotBlank())Text(ov,fontSize=11.sp,lineHeight=15.sp,color=Muted,maxLines=3,overflow=TextOverflow.Ellipsis,modifier=Modifier.padding(top=7.dp))}
        Box(Modifier.size(32.dp).clip(CircleShape).background(Color(0xFF241A1A)).border(1.dp,Color(0xFF4A3434),CircleShape).clickable(onClick=onDelete),contentAlignment=Alignment.Center){Text("×",fontSize=24.sp,color=Color(0xFFFFB8B8),modifier=Modifier.offset(y=(-1).dp))}
    }
}

private data class EditableChannel(val key:String,val name:String,val group:String,val number:String,val detail:String,val picon:String,val visible:Boolean)
@Composable private fun ChannelEditorDialog(api:HomeApi,onDismiss:()->Unit){
    val scope=rememberCoroutineScope();var channels by remember{mutableStateOf(emptyList<EditableChannel>())};var meta by remember{mutableStateOf("Завантаження…")};var busy by remember{mutableStateOf(false)}
    suspend fun reload(extra:String=""){if(api.server==null)return;busy=true;runCatching{api.get("/api/tv/channels/edit")}.onSuccess{j->channels=j.optJSONArray("channels")?.objects().orEmpty().map{c->EditableChannel(c.optString("stable_key"),c.optString("display_name",c.optString("name","Канал")),c.optString("group","TV"),c.optString("number"),c.optString("tvg_id",c.optString("stable_key")),c.optString("picon"),c.optBoolean("visible",true))};meta=extra.ifBlank{"${channels.count{it.visible}} з ${channels.size} каналів видимі"}}.onFailure{meta=it.message?:"Редактор недоступний"};busy=false}
    LaunchedEffect(api.server){reload()}
    fun setVisible(index:Int,v:Boolean){channels=channels.toMutableList().also{it[index]=it[index].copy(visible=v)};meta="${channels.count{it.visible}} з ${channels.size} каналів видимі"}
    fun move(index:Int,dir:Int){val ni=(index+dir).coerceIn(0,channels.lastIndex);if(ni==index)return;channels=channels.toMutableList().also{val x=it.removeAt(index);it.add(ni,x)}}
    Dialog(onDismissRequest=onDismiss,properties=DialogProperties(usePlatformDefaultWidth=false)){
        Box(Modifier.fillMaxSize().background(Color.Black.copy(alpha=.72f)).clickable(onClick=onDismiss),contentAlignment=Alignment.BottomCenter){
            Column(Modifier.fillMaxWidth().widthIn(max=500.dp).fillMaxHeight(.94f).clip(RoundedCornerShape(topStart=18.dp,topEnd=18.dp)).background(Input).border(1.dp,Line,RoundedCornerShape(topStart=18.dp,topEnd=18.dp)).clickable(enabled=false){}){
                Row(Modifier.fillMaxWidth().height(52.dp).padding(horizontal=9.dp),verticalAlignment=Alignment.CenterVertically){TextButton(onClick=onDismiss,modifier=Modifier.width(92.dp)){Text(ui("Скасувати"),color=Muted,fontSize=13.sp,fontWeight=FontWeight.Bold)};Text(ui("Канали"),fontSize=17.sp,fontWeight=FontWeight.Bold,color=Text,textAlign=androidx.compose.ui.text.style.TextAlign.Center,modifier=Modifier.weight(1f));TextButton(onClick={scope.launch{busy=true;val order=JSONArray();channels.forEach{order.put(it.key)};val vis=JSONArray();channels.filter{it.visible}.forEach{vis.put(it.key)};runCatching{api.put("/api/tv/channels/edit",JSONObject().put("order",order).put("visible",vis))}.onSuccess{onDismiss()}.onFailure{meta=it.message?:"Не вдалося зберегти"};busy=false}},enabled=!busy,modifier=Modifier.width(92.dp)){Text(ui("Зберегти"),color=Accent,fontSize=13.sp,fontWeight=FontWeight.Bold)}}
                Row(Modifier.fillMaxWidth().height(58.dp).padding(horizontal=12.dp),verticalAlignment=Alignment.CenterVertically){Text(meta,fontSize=10.sp,color=Muted,modifier=Modifier.weight(1f));Button(onClick={scope.launch{busy=true;meta="Запускаю перевірку GitHub…";runCatching{api.post("/api/tv/refresh",JSONObject().put("force",true))}.onSuccess{repeat(40){delay(1500);val st=runCatching{api.get("/api/tv/refresh")}.getOrNull()?:return@repeat;if(st.optString("status")!="running"){reload(if((st.optJSONArray("added")?.length()?:0)+(st.optJSONArray("updated")?.length()?:0)+(st.optJSONArray("removed")?.length()?:0)>0)"Список оновлено" else "GitHub перевірено · змін немає");return@launch}};meta="Перевірка продовжується у фоні"}.onFailure{meta=it.message?:"Оновлення не запущено"};busy=false}},enabled=!busy,shape=RoundedCornerShape(10.dp),colors=ButtonDefaults.buttonColors(containerColor=Color(0xFF22282D)),contentPadding=PaddingValues(horizontal=11.dp),modifier=Modifier.height(36.dp)){Text(ui("Оновити з GitHub"),fontSize=10.sp,fontWeight=FontWeight.Bold,color=Text)}}
                LazyColumn(Modifier.weight(1f).fillMaxWidth().padding(horizontal=10.dp),contentPadding=PaddingValues(bottom=18.dp)){
                    items(channels.size,key={channels[it].key}){i->val c=channels[i];ChannelEditorRow(api,c,{setVisible(i,it)},{dir->move(i,dir)})}
                }
            }
        }
    }
}

@Composable private fun ChannelEditorRow(api:HomeApi,c:EditableChannel,onVisible:(Boolean)->Unit,onMove:(Int)->Unit){
    val bm=remoteBitmap(if(c.picon.isBlank())null else if(c.picon.startsWith("http"))c.picon else api.server+c.picon,128,128);var drag by remember{mutableFloatStateOf(0f)}
    Row(Modifier.fillMaxWidth().heightIn(min=112.dp).padding(horizontal=3.dp,vertical=9.dp),verticalAlignment=Alignment.CenterVertically){
        Box(Modifier.width(64.dp).height(96.dp).clip(RoundedCornerShape(8.dp)).background(Color(0xFFF4F5F6)),contentAlignment=Alignment.Center){if(bm!=null)Image(bm,null,Modifier.fillMaxSize().padding(5.dp),contentScale=ContentScale.Fit)else Text(c.name.split(" ").filter{it.isNotBlank()}.take(2).joinToString(""){it.take(1)}.uppercase(Locale.ROOT).ifBlank{"TV"},fontSize=18.sp,fontWeight=FontWeight.ExtraBold,color=Color(0xFF24292E))}
        Column(Modifier.weight(1f).padding(start=11.dp)){Text(c.name,fontSize=15.sp,fontWeight=FontWeight.SemiBold,color=Text,maxLines=2,overflow=TextOverflow.Ellipsis);Text(listOf(c.group,c.number.takeIf{it.isNotBlank()}?.let{"Канал $it"},if(c.visible)"У списку" else "Прихований").filterNotNull().joinToString(" · "),fontSize=10.sp,fontWeight=FontWeight.Bold,color=Accent,modifier=Modifier.padding(top=4.dp));if(c.detail.isNotBlank())Text(c.detail,fontSize=11.sp,color=Muted,maxLines=2,overflow=TextOverflow.Ellipsis,modifier=Modifier.padding(top=6.dp))}
        Column(Modifier.width(48.dp),horizontalAlignment=Alignment.CenterHorizontally){Box(Modifier.size(26.dp).clip(CircleShape).border(2.dp,if(c.visible)Accent else Muted,CircleShape).background(if(c.visible)Accent else Color.Transparent).clickable{onVisible(!c.visible)},contentAlignment=Alignment.Center){if(c.visible)Text("✓",fontSize=16.sp,fontWeight=FontWeight.Bold,color=Color.White)};Text("⋮⋮",fontSize=22.sp,color=Muted,modifier=Modifier.padding(top=12.dp).pointerInput(c.key){detectDragGesturesAfterLongPress(onDragStart={drag=0f},onDragEnd={drag=0f},onDragCancel={drag=0f}){change,delta->change.consume();drag+=delta.y;if(abs(drag)>52f){onMove(if(drag>0)1 else -1);drag=0f}}})}
    }
}

private data class SaverType(val value:String,val title:String,val meta:String,val poster:String)

@Composable
private fun ScreensaverDialog(api:HomeApi,onDismiss:()->Unit){
    val scope=rememberCoroutineScope()
    var enabled by remember{mutableStateOf(false)}
    var idle by remember{mutableIntStateOf(60)}
    var screenOff by remember{mutableIntStateOf(0)}
    var type by remember{mutableStateOf("gallery")}
    var types by remember{mutableStateOf(listOf(SaverType("gallery","Галерея","Мистецтво з усього світу","/static/screensaver-previews/gallery.webp")))}
    var typeOpen by remember{mutableStateOf(false)}
    var typeQuery by remember{mutableStateOf("")}
    var hiddenRenderers by remember{mutableStateOf(setOf<String>())}
    var message by remember{mutableStateOf("Завантаження…")}
    var busy by remember{mutableStateOf(false)}

    suspend fun load(){
        if(api.server==null)return
        runCatching{api.get("/api/screensaver")}.onSuccess{j->
            enabled=j.optBoolean("enabled")
            idle=j.optInt("idle_seconds",60)
            screenOff=j.optInt("screen_off_seconds",0)
            type=j.optString("type","gallery")
            hiddenRenderers=j.optJSONArray("hidden_renderers")?.strings()?.toSet().orEmpty()
            val extra=j.optJSONArray("renderers")?.objects().orEmpty().mapNotNull{x->
                x.optString("value").takeIf{it.isNotBlank()}?.let{v->SaverType(v,x.optString("title",x.optString("name",v)),x.optString("meta","XScreenSaver"),x.optString("poster","/static/screensaver-previews/gallery.webp"))}
            }
            types=(listOf(SaverType("gallery","Галерея","Мистецтво з усього світу","/static/screensaver-previews/gallery.webp"))+extra).distinctBy{it.value}.filterNot{it.value in hiddenRenderers}
            message=""
        }.onFailure{message="Не вдалося прочитати налаштування."}
    }
    suspend fun save(){
        busy=true
        runCatching{api.put("/api/screensaver",JSONObject().put("enabled",enabled).put("idle_seconds",idle).put("screen_off_seconds",screenOff).put("type",type).put("hidden_renderers",JSONArray().also{a->hiddenRenderers.sorted().forEach{a.put(it)}}))}
            .onSuccess{message="Збережено"}
            .onFailure{message=it.message?:"Не вдалося зберегти налаштування."}
        busy=false
    }
    LaunchedEffect(api.server){load()}

    Dialog(onDismissRequest=onDismiss,properties=DialogProperties(usePlatformDefaultWidth=false)){
        Box(
            Modifier.fillMaxSize().background(Color.Black.copy(alpha=.68f)).clickable(onClick=onDismiss),
            contentAlignment=Alignment.Center
        ){
            Column(
                Modifier.fillMaxWidth(.90f).widthIn(max=360.dp).clip(RoundedCornerShape(24.dp)).background(Color(0xFF191D22)).border(1.dp,Color(0xFF383E45),RoundedCornerShape(24.dp)).clickable(enabled=false){}.padding(horizontal=22.dp,vertical=22.dp),
                horizontalAlignment=Alignment.CenterHorizontally
            ){
                Row(Modifier.fillMaxWidth(),horizontalArrangement=Arrangement.End){Text("×",fontSize=30.sp,color=Text,modifier=Modifier.clickable(onClick=onDismiss))}
                Mdi("monitor-dashboard",44.dp,Accent)
                Text(ui("Скрінсейвер"),fontSize=22.sp,fontWeight=FontWeight.Bold,color=Text,modifier=Modifier.padding(top=10.dp,bottom=14.dp))
                Row(Modifier.fillMaxWidth().height(60.dp),verticalAlignment=Alignment.CenterVertically){
                    Column(Modifier.weight(1f)){
                        Text(ui("Увімкнено"),fontSize=14.sp,fontWeight=FontWeight.SemiBold,color=Text)
                        Text(ui("Запуск лише коли медіа не відтворюється"),fontSize=14.sp,lineHeight=20.sp,color=Muted,modifier=Modifier.padding(top=5.dp))
                    }
                    SkeletonSwitch(enabled,{enabled=it;scope.launch{save()}},48.dp,28.dp,22.dp)
                }
                SaverSelectRow("Після простою",idleLabel(idle),listOf(60,120,300,600,900,1800).map{idleLabel(it)},enabled){v->
                    idle=listOf(60,120,300,600,900,1800).firstOrNull{idleLabel(it)==v}?:60
                    scope.launch{save()}
                }
                val screenOffValues=listOf(0,300,600,900,1800,3600,7200,14400)
                fun screenOffLabel(v:Int)=when(v){0->"Ніколи";300->"5 хвилин";600->"10 хвилин";900->"15 хвилин";1800->"30 хвилин";3600->"1 година";7200->"2 години";14400->"4 години";else->"Ніколи"}
                SaverSelectRow("Вимкнути екран",screenOffLabel(screenOff),screenOffValues.map(::screenOffLabel),true){v->
                    screenOff=screenOffValues.firstOrNull{screenOffLabel(it)==v}?:0
                    scope.launch{save()}
                }
                val activeType=types.firstOrNull{it.value==type}?:types.first()
                Column(Modifier.fillMaxWidth().padding(vertical=12.dp)){
                    Text(ui("Тип"),fontSize=13.sp,color=Text)
                    Row(
                        Modifier.fillMaxWidth().height(52.dp).padding(top=6.dp).clip(RoundedCornerShape(10.dp)).background(Color(0xFF252A30)).border(1.dp,Color(0xFF3B4249),RoundedCornerShape(10.dp)).clickable(enabled=enabled){typeOpen=true}.padding(horizontal=9.dp),
                        verticalAlignment=Alignment.CenterVertically
                    ){
                        val bm=remoteBitmap(if(api.server!=null)api.server+activeType.poster else null,240,360)
                        Box(Modifier.width(28.dp).height(42.dp).clip(RoundedCornerShape(5.dp)).background(Bg),contentAlignment=Alignment.Center){if(bm!=null)Image(bm,null,Modifier.fillMaxSize(),contentScale=ContentScale.Crop)}
                        Text(activeType.title,fontSize=13.sp,color=if(enabled)Text else Muted,modifier=Modifier.weight(1f).padding(start=9.dp))
                        Text("›",fontSize=28.sp,color=Muted)
                    }
                }
                Button(
                    onClick={scope.launch{
                        busy=true
                        runCatching{save();api.post("/api/screensaver/start",JSONObject().put("type",type))}.onSuccess{message="Запущено"}.onFailure{message=it.message?:"Не вдалося запустити скрінсейвер."}
                        busy=false
                    }},
                    enabled=!busy,
                    shape=RoundedCornerShape(14.dp),
                    colors=ButtonDefaults.buttonColors(containerColor=Accent),
                    modifier=Modifier.fillMaxWidth().height(52.dp).padding(top=8.dp)
                ){Text(ui("Запустити"),fontSize=14.sp,fontWeight=FontWeight.ExtraBold,color=Color.White)}
                Text(message,fontSize=12.sp,color=Muted,modifier=Modifier.padding(top=9.dp).heightIn(min=18.dp))
            }
        }
    }

    if(typeOpen){
        Dialog(onDismissRequest={typeOpen=false},properties=DialogProperties(usePlatformDefaultWidth=false)){
            Box(Modifier.fillMaxSize().background(Color.Black.copy(alpha=.78f)).clickable{typeOpen=false},contentAlignment=Alignment.BottomCenter){
                Column(Modifier.fillMaxWidth().widthIn(max=500.dp).fillMaxHeight(.92f).clip(RoundedCornerShape(topStart=18.dp,topEnd=18.dp)).background(Input).clickable(enabled=false){}){
                    Row(Modifier.fillMaxWidth().height(52.dp).padding(horizontal=15.dp),verticalAlignment=Alignment.CenterVertically){
                        Text(ui("Скрінсейвери"),fontSize=17.sp,fontWeight=FontWeight.Bold,color=Text,modifier=Modifier.weight(1f))
                        Text(ui("Закрити"),fontSize=13.sp,color=Muted,modifier=Modifier.clickable{typeOpen=false})
                    }
                    Column(Modifier.fillMaxWidth().padding(horizontal=12.dp,vertical=10.dp)){Row(Modifier.fillMaxWidth().height(42.dp).clip(RoundedCornerShape(12.dp)).background(Color(0xFF0B1014)).border(1.dp,Color(0xFF353C42),RoundedCornerShape(12.dp)).padding(horizontal=11.dp),verticalAlignment=Alignment.CenterVertically){Mdi("magnify",20.dp,Muted);Spacer(Modifier.width(8.dp));androidx.compose.foundation.text.BasicTextField(value=typeQuery,onValueChange={typeQuery=it},singleLine=true,textStyle=androidx.compose.ui.text.TextStyle(color=Text,fontSize=14.sp),modifier=Modifier.weight(1f),decorationBox={inner->if(typeQuery.isEmpty())Text(ui("Знайти ефект"),color=Color(0xFF747D85),fontSize=14.sp);inner()})};Text("${types.count{typeQuery.isBlank()||(it.title+" "+it.meta).contains(typeQuery,true)}} ефектів",fontSize=10.sp,color=Muted,modifier=Modifier.padding(start=3.dp,top=6.dp))}
                    val visibleTypes=types.filter{typeQuery.isBlank()||(it.title+" "+it.meta).contains(typeQuery,true)}
                    LazyColumn(Modifier.weight(1f).padding(horizontal=10.dp),contentPadding=PaddingValues(bottom=18.dp)){items(visibleTypes,key={it.value}){x->SaverTypeRow(api,x,x.value==type){type=x.value;typeOpen=false;scope.launch{save()}}}}
                }
            }
        }
    }
}

private fun idleLabel(v:Int)=when(v){60->"1 хвилина";120->"2 хвилини";300->"5 хвилин";600->"10 хвилин";900->"15 хвилин";1800->"30 хвилин";else->"${v/60} хвилин"}

@Composable
private fun SaverSelectRow(label:String,value:String,options:List<String>,enabled:Boolean,onSelect:(String)->Unit){
    var open by remember{mutableStateOf(false)}
    Row(Modifier.fillMaxWidth().height(64.dp),verticalAlignment=Alignment.CenterVertically){
        Text(label,fontSize=13.sp,color=Text,modifier=Modifier.width(120.dp))
        Box(Modifier.weight(1f)){
            Row(
                Modifier.fillMaxWidth().height(40.dp).clip(RoundedCornerShape(10.dp)).background(Color(0xFF252A30)).border(1.dp,Color(0xFF3B4249),RoundedCornerShape(10.dp)).clickable(enabled=enabled){open=true}.padding(horizontal=10.dp),
                verticalAlignment=Alignment.CenterVertically
            ){
                Text(value,fontSize=13.sp,color=if(enabled)Text else Muted,modifier=Modifier.weight(1f))
                Mdi("chevron-down",16.dp,Muted)
            }
            DropdownMenu(expanded=open,onDismissRequest={open=false},modifier=Modifier.background(Color(0xFF252A30))){
                options.forEach{x->DropdownMenuItem(text={Text(x,color=Text,fontSize=13.sp)},onClick={open=false;onSelect(x)})}
            }
        }
    }
}

@Composable
private fun SaverTypeRow(api:HomeApi,x:SaverType,active:Boolean,onSelect:()->Unit){
    val bm=remoteBitmap(if(api.server!=null)api.server+x.poster else null,192,288)
    Row(Modifier.fillMaxWidth().heightIn(min=112.dp).clickable(onClick=onSelect).padding(horizontal=3.dp,vertical=9.dp),verticalAlignment=Alignment.CenterVertically){
        Box(Modifier.width(64.dp).height(96.dp).clip(RoundedCornerShape(8.dp)).background(Bg),contentAlignment=Alignment.Center){if(bm!=null)Image(bm,null,Modifier.fillMaxSize(),contentScale=ContentScale.Crop)}
        Column(Modifier.weight(1f).padding(start=11.dp)){
            Text(x.title,fontSize=15.sp,fontWeight=FontWeight.SemiBold,color=if(active)Color(0xFFD7EF80) else Text)
            Text(x.meta,fontSize=10.sp,fontWeight=FontWeight.SemiBold,color=Muted,modifier=Modifier.padding(top=4.dp))
        }
        Box(Modifier.size(26.dp).clip(CircleShape).border(2.dp,if(active)Color(0xFFD7EF80) else Color(0xFF7F898F),CircleShape).background(if(active)Color(0xFFD7EF80) else Color.Transparent),contentAlignment=Alignment.Center){if(active)Text("✓",fontSize=16.sp,fontWeight=FontWeight.Bold,color=Input)}
    }
}

@Composable
private fun Header(title:String, connected:Boolean, hyperion:Boolean, onHyperion:(Boolean)->Unit, outputTarget:String, onOutputTarget:(String)->Unit, onMenu:()->Unit, subtitle:String="Home Edge", ambientColor:Color?=null) {
    // Top controls must remain readable regardless of the poster palette.
    val headerColor=Color(0xFFF3F6F8)
    Row(Modifier.fillMaxWidth().height(74.dp).background(Color(0x8A070A0D)).padding(start=16.dp,end=10.dp,top=8.dp,bottom=6.dp),verticalAlignment=Alignment.CenterVertically) {
        Column(Modifier.weight(1f)) {
            Text(title,fontSize=20.sp,fontWeight=FontWeight.Bold,color=headerColor,maxLines=1)
            Row(verticalAlignment=Alignment.CenterVertically) {
                Text(if(connected) "Підключено" else "Недоступно",fontSize=11.sp,fontWeight=FontWeight.SemiBold,color=Color(0xFFD4DBE1),maxLines=1)
                Spacer(Modifier.width(6.dp)); Box(Modifier.size(6.dp).clip(CircleShape).background(if(connected)Green else Muted))
            }
        }
        OutputTargetSelector(outputTarget,onOutputTarget)
        Spacer(Modifier.width(7.dp))
        Mdi(if(hyperion)"lightbulb-on-outline" else "lightbulb-outline",21.dp,if(hyperion)Color(0xFFFF8B38) else Color(0xFFE4E9ED))
        Spacer(Modifier.width(3.dp))
        SkeletonSwitch(hyperion,onHyperion,42.dp,24.dp,18.dp)
        IconButton(onClick=onMenu,modifier=Modifier.size(40.dp)){Mdi("dots-vertical",23.dp,Color(0xFFF3F6F8))}
    }
}


@Composable private fun OutputTargetSelector(target:String,onTarget:(String)->Unit){
    var open by remember{mutableStateOf(false)}
    val samsung=target=="samsung"
    Box{
        Row(Modifier.height(38.dp).width(148.dp).clip(RoundedCornerShape(19.dp)).background(Color(0xFF171D23)).border(1.dp,Line,RoundedCornerShape(19.dp)).clickable{open=true}.padding(horizontal=12.dp),verticalAlignment=Alignment.CenterVertically,horizontalArrangement=Arrangement.spacedBy(8.dp)){
            Mdi(if(samsung)"devices" else "television",18.dp,Color(0xFFD8DDE2))
            Text(if(samsung)"Samsung" else "TV",fontSize=11.sp,fontWeight=FontWeight.SemiBold,color=Text,maxLines=1,modifier=Modifier.weight(1f))
            Mdi("chevron-down",14.dp,Muted)
        }
        DropdownMenu(expanded=open,onDismissRequest={open=false},modifier=Modifier.width(148.dp).background(Color(0xFF171B20)).border(1.dp,Line,RoundedCornerShape(12.dp))){
            DropdownMenuItem(text={Text("TV",color=Text,fontSize=13.sp)},leadingIcon={Mdi("television",19.dp,if(!samsung)Accent else Muted)},onClick={open=false;if(target!="tv")onTarget("tv")})
            DropdownMenuItem(text={Text("Samsung",color=Text,fontSize=13.sp)},leadingIcon={Mdi("devices",19.dp,if(samsung)Accent else Muted)},onClick={open=false;if(target!="samsung")onTarget("samsung")})
        }
    }
}


@Composable private fun SkeletonSwitch(checked:Boolean,onChange:(Boolean)->Unit,width:Dp=50.dp,height:Dp=28.dp,thumb:Dp=22.dp){
    Box(Modifier.width(width).height(height).clip(CircleShape).background(if(checked)Accent else Color(0xFF414952)).clickable{onChange(!checked)}.padding(3.dp),contentAlignment=if(checked)Alignment.CenterEnd else Alignment.CenterStart){Box(Modifier.size(thumb).clip(CircleShape).background(Color.White).shadow(3.dp,CircleShape))}
}

@Composable private fun SkeletonRange(value:Float,onChange:(Float)->Unit,onFinished:()->Unit,minValue:Float,maxValue:Float,modifier:Modifier=Modifier){
    var widthPx by remember{mutableIntStateOf(1)}
    val fraction=((value-minValue)/(maxValue-minValue).coerceAtLeast(.0001f)).coerceIn(0f,1f)
    Box(modifier.height(22.dp).onSizeChanged{widthPx=it.width}.pointerInput(widthPx,minValue,maxValue){
        fun update(x:Float){onChange(minValue+(maxValue-minValue)*(x/widthPx.toFloat()).coerceIn(0f,1f))}
        detectHorizontalDragGestures(onDragStart={update(it.x)},onHorizontalDrag={change,_->update(change.position.x);change.consume()},onDragEnd=onFinished,onDragCancel=onFinished)
    }){Canvas(Modifier.fillMaxSize().pointerInput(widthPx){detectTapGestures(onTap={p->onChange(minValue+(maxValue-minValue)*(p.x/widthPx.toFloat()).coerceIn(0f,1f));onFinished()})}){val y=size.height/2;val x=size.width*fraction;drawLine(Color(0xFF343B42),Offset(0f,y),Offset(size.width,y),4.dp.toPx());drawLine(Accent,Offset(0f,y),Offset(x,y),4.dp.toPx());drawCircle(Color.White,7.5.dp.toPx(),Offset(x,y))}}
}

@Composable private fun BottomNav(active:Int,items:List<NavDef>,onSelect:(Int)->Unit) {
    Row(Modifier.fillMaxWidth().height(70.dp).navigationBarsPadding().background(Bg.copy(alpha=.98f)).padding(horizontal=10.dp,vertical=6.dp)) {
        items.forEachIndexed { i,n ->
            val on=i==active; Column(Modifier.weight(1f).fillMaxHeight().clickable{onSelect(i)},horizontalAlignment=Alignment.CenterHorizontally,verticalArrangement=Arrangement.Center) {
                Mdi(n.icon,21.dp,if(on)Accent else Muted); Spacer(Modifier.height(3.dp)); Text(n.title,fontSize=9.sp,lineHeight=10.sp,fontWeight=if(on)FontWeight.Bold else FontWeight.SemiBold,color=if(on)Accent else Muted)
            }
        }
    }
}

@Composable private fun ModeRow(mode:String,ambientColor:Color?,includeGames:Boolean=true,onMode:(String)->Unit) {
    val compact=LocalConfiguration.current.screenHeightDp<=760;val h=if(compact)58.dp else 64.dp;val iconSize=if(compact)20.dp else 22.dp
    val defs=if(includeGames) listOf(Triple("YouTube","youtube","kiosk"),Triple("Cast","cast-connected","mpv"),Triple("TV","television","tv"),Triple("Games","gamepad-variant","games")) else listOf(Triple("YouTube","youtube","kiosk"),Triple("Відео","cast-connected","mpv"),Triple("TV","television","tv"))
    Row(Modifier.fillMaxWidth().height(h),horizontalArrangement=Arrangement.spacedBy(6.dp)){defs.forEach{(label,icon,target)->
        val on=mode==target
        Column(
            Modifier.weight(1f).fillMaxHeight().clickable{onMode(target)},
            horizontalAlignment=Alignment.CenterHorizontally,verticalArrangement=Arrangement.Center
        ){
            // Keep top mode controls visually consistent with BottomNav: no card, outline or fill.
            // Contrast comes from fixed foreground colors over the ambient blur.
            val idleColor=Color(0xFFE5E9EC);val activeColor=Color(0xFFFF6D00)
            Mdi(icon,iconSize,if(on)activeColor else idleColor);Spacer(Modifier.height(4.dp));
            Text(label,fontSize=10.sp,fontWeight=if(on)FontWeight.Bold else FontWeight.SemiBold,color=if(on)activeColor else idleColor)
        }
    }}
}


@Composable private fun HomeScreen(api:HomeApi,active:Boolean,hyperion:Boolean,onHyperion:(Boolean)->Unit,outputTarget:String,onOutputTarget:(String)->Unit,onMenu:()->Unit) {
    val scope=rememberCoroutineScope()
    var mode by remember{mutableStateOf("unknown")}
    var youtubePlayer by remember{mutableStateOf(JSONObject())}
    var youtubeRemotePlayer by remember{mutableStateOf(JSONObject())}
    var videoPlayer by remember{mutableStateOf(JSONObject())}
    var tvPlayer by remember{mutableStateOf(JSONObject())}
    var volume by remember{mutableIntStateOf(25)}
    var lastNonzero by remember{mutableIntStateOf(25)}
    suspend fun refresh(){
        if(api.server==null)return
        if(outputTarget=="samsung"){
            runCatching{api.get("/api/samsung/media/status")}.onSuccess{ss->
                mode=when(ss.optString("mode","video")){"youtube"->"kiosk";"tv"->"tv";else->"mpv"}
                val samsungTitle=ss.optString("display_title","").ifBlank{"Samsung · "+when(mode){"kiosk"->"YouTube";"tv"->"TV";else->"Відео"}}
                val samsungRunning=if(mode=="kiosk")ss.optBoolean("active",false) else ss.optBoolean("online",false)
                val sp=JSONObject().put("running",samsungRunning).put("pause",!ss.optBoolean("playing",false)).put("time-pos",ss.optDouble("position_seconds",0.0)).put("duration",ss.optDouble("duration_seconds",0.0)).put("display-title",samsungTitle)
                if(mode=="kiosk")sp.put("backend","smarttube")
                listOf("poster","poster_landscape","episode","season","quality","translation","job_id","source_id","video_id","channel").forEach{k->if(ss.has(k)&&!ss.isNull(k)&&ss.optString(k).isNotBlank())sp.put(k,ss.get(k))}
                if(ss.has("is_live"))sp.put("is_live",ss.optBoolean("is_live",false))
                if(ss.has("is_seekable"))sp.put("is_seekable",ss.optBoolean("is_seekable",false))
                ss.optString("media_title","").takeIf{it.isNotBlank()}?.let{sp.put("media-title",it)}
                when(mode){
                    "kiosk"->{
                        youtubePlayer=sp
                        youtubeRemotePlayer=JSONObject().put("video_id",ss.optString("video_id","")).put("time",ss.optDouble("position_seconds",0.0)).put("duration",ss.optDouble("duration_seconds",0.0)).put("is_live",ss.optBoolean("is_live",false)).put("is_seekable",ss.optBoolean("is_seekable",false)).put("state",if(ss.optBoolean("playing",false))1 else 2)
                    }
                    "tv"->tvPlayer=sp
                    else->videoPlayer=sp
                }
            }
        }else{
            val newMode=runCatching{api.get("/api/mode")}.getOrNull()?.let{it.optString("mode",it.optString("tv_mode","unknown"))}?:mode
            val p=runCatching{api.get("/api/player")}.getOrNull()
            mode=newMode
            if(p!=null)when(newMode){"kiosk"->youtubePlayer=p;"tv"->tvPlayer=p;else->videoPlayer=p}
            if(newMode=="kiosk")runCatching{api.get("/api/remote/status")}.onSuccess{youtubeRemotePlayer=it.optJSONObject("youtube_player")?:JSONObject()}
            runCatching{api.get("/api/volume")}.onSuccess{volume=it.optInt("master",volume);if(volume>0)lastNonzero=volume}
        }
    }
    LaunchedEffect(api.server,active){if(api.server!=null){refresh();if(active)while(true){delay(2500);refresh()}}}
    val ambientPoster=when(mode){"kiosk"->youtubePlayer.optString("poster_landscape",youtubePlayer.optString("poster",""));"tv"->"";else->videoPlayer.optString("poster",videoPlayer.optString("poster_landscape",""))}
    val ambientBitmap=remoteBitmap(if(ambientPoster.isNotBlank()&&api.server!=null)api.server+ambientPoster else null,720,1080)
    val ambientUiColor=remember(ambientBitmap){ambientComplement(ambientBitmap)}
    Box(Modifier.fillMaxSize()){
        AmbientPosterBackground(ambientBitmap,Modifier.fillMaxSize())
        Column(Modifier.fillMaxSize()) {
        Header(ui("Головна"),api.server!=null,hyperion,onHyperion,outputTarget,onOutputTarget,onMenu,ambientColor=ambientUiColor.takeIf{ambientBitmap!=null})
        Column(Modifier.weight(1f).padding(horizontal=13.dp)) {
            ModeRow(mode,ambientUiColor.takeIf{ambientBitmap!=null},includeGames=outputTarget!="samsung"){target->if(target!=mode){mode=target;scope.launch{if(outputTarget=="samsung"){val sm=when(target){"kiosk"->"youtube";"tv"->"tv";else->"video"};runCatching{api.post("/api/samsung/media/mode/$sm")}}else runCatching{api.post("/api/mode/$target")};refresh()}}}
            Spacer(Modifier.height(6.dp))
            PersistentHomeRemotes(
                api=api,
                active=active,
                mode=mode,
                youtubePlayer=youtubePlayer,
                youtubeRemotePlayer=youtubeRemotePlayer,
                videoPlayer=videoPlayer,
                tvPlayer=tvPlayer,
                volume=volume,
                onVolume={v->volume=v;if(v>0)lastNonzero=v;scope.launch{runCatching{api.post("/api/volume",JSONObject().put("level",v))}}},
                onRemote={act->scope.launch{if(outputTarget=="samsung")runCatching{api.post("/api/samsung/media/control/$act")}else runCatching{api.post("/api/remote/control",JSONObject().put("action",act).put("phase","tap"))}}},
                onControl={ctl->scope.launch{runCatching{api.post("/api/control/$ctl")};refresh()}},
                onSeek={pos->scope.launch{runCatching{api.post("/api/seek",JSONObject().put("position",pos))};refresh()}},
                onMute={scope.launch{val v=if(volume==0)lastNonzero.coerceAtLeast(1) else 0;runCatching{api.post("/api/volume",JSONObject().put("level",v))};refresh()}}
            )
        }
        }
    }
}

@OptIn(ExperimentalFoundationApi::class)
@Composable private fun PlayerTitleLine(title:String,top:Dp){Text(title,Modifier.fillMaxWidth().padding(top=top).basicMarquee(iterations=Int.MAX_VALUE),fontSize=14.sp,fontWeight=FontWeight.SemiBold,color=Text,maxLines=1,softWrap=false,textAlign=androidx.compose.ui.text.style.TextAlign.Center)}

@Composable private fun PersistentHomeRemotes(api:HomeApi,active:Boolean,mode:String,youtubePlayer:JSONObject,youtubeRemotePlayer:JSONObject,videoPlayer:JSONObject,tvPlayer:JSONObject,volume:Int,onVolume:(Int)->Unit,onRemote:(String)->Unit,onControl:(String)->Unit,onSeek:(Int)->Unit,onMute:()->Unit){
    // Exactly three permanent remote surfaces. Games reuses the video surface; it never creates a fourth remote.
    val panel=when(mode){"kiosk"->0;"tv"->2;else->1}
    Box(Modifier.fillMaxSize()){
        PersistentTabSlot(panel==0){YoutubeRemoteCard(api,youtubePlayer,youtubeRemotePlayer,volume,onVolume,onRemote,onSeek,onMute)}
        PersistentTabSlot(panel==1){VideoRemoteCard(api,videoPlayer,volume,onVolume,onControl,onSeek,onMute)}
        PersistentTabSlot(panel==2){TvRemoteCard(api,active,tvPlayer,volume,onVolume,onControl,onMute)}
    }
}

@Composable private fun YoutubeRemoteCard(api:HomeApi,p:JSONObject,remote:JSONObject,volume:Int,onVolume:(Int)->Unit,onRemote:(String)->Unit,onSeek:(Int)->Unit,onMute:()->Unit){
    var youtubeControls by remember{mutableStateOf(JSONObject())}
    var liveRemote by remember(remote){mutableStateOf(remote)}
    var lastGoodPlayer by remember{mutableStateOf(JSONObject())}
    val samsungSmartTube=p.optString("backend")=="smarttube"
    LaunchedEffect(p,remote){
        if(p.optString("backend") in setOf("youtube-web","smarttube")) lastGoodPlayer=p
        if(samsungSmartTube) liveRemote=remote
    }
    LaunchedEffect(api.server,samsungSmartTube){
        if(api.server!=null&&!samsungSmartTube)while(true){
            runCatching{api.get("/api/remote/status")}.onSuccess{st->
                st.optJSONObject("youtube_context")?.optJSONObject("controls")?.let{youtubeControls=it}
                val yp=st.optJSONObject("youtube_player")
                val player=st.optJSONObject("player")
                val playerId=player?.optString("video_id","").orEmpty()
                val remoteId=yp?.optString("video_id","").orEmpty()
                if(yp!=null) liveRemote=yp
                if(player!=null&&player.optString("backend")=="youtube-web"&&(remoteId.isBlank()||playerId.isBlank()||playerId==remoteId)) lastGoodPlayer=player
            }
            delay(1500)
        }
    }
    val running=lastGoodPlayer.optBoolean("running") || liveRemote.optString("video_id").isNotBlank()
    val title=if(running)lastGoodPlayer.optString("display-title",lastGoodPlayer.optString("media-title","Відео")) else "Нічого не відтворюється"
    val se=if(running&&p.has("season")&&!p.isNull("season")){val s=p.optString("season","").takeUnless{it.equals("null",true)}.orEmpty();val e=if(p.has("episode")&&!p.isNull("episode"))p.optString("episode","").filter{it.isDigit()} else "";if(s.isNotBlank())"S$s"+(if(e.isNotBlank())" · E$e" else "") else ""}else ""
    // YouTube card is always 16:9. A portrait fallback contains an intentionally blurred
    // background and appears as a smear while the new landscape thumbnail is still loading.
    // Show the YouTube logo until the correct landscape poster exists instead.
    val poster=lastGoodPlayer.optString("poster_landscape","")
    val posterUrl=if(poster.isBlank())null else if(poster.startsWith("http://")||poster.startsWith("https://"))poster else if(api.server!=null)api.server+poster else null
    val bitmap=remoteBitmap(posterUrl,960,540)
    var seek by remember{mutableFloatStateOf(0f)}
    val videoId=liveRemote.optString("video_id","")
    val isLive=liveRemote.optBoolean("is_live",false)
    val canSeek=liveRemote.optBoolean("is_seekable",!isLive&&liveRemote.optDouble("duration",0.0)>1.0)
    val serverPos=liveRemote.optDouble("time",0.0).toFloat()
    val rawDuration=liveRemote.optDouble("duration",0.0).toFloat()
    val duration=rawDuration.coerceAtLeast(1f)
    LaunchedEffect(videoId,serverPos,rawDuration){seek=serverPos.coerceIn(0f,duration)}
    Column(Modifier.fillMaxSize().clip(RoundedCornerShape(16.dp)).background(Card)){
        Box(Modifier.fillMaxWidth().aspectRatio(16f/9f).background(Color(0xFF080A0C)),contentAlignment=Alignment.Center){
            if(bitmap!=null) Image(bitmap,null,Modifier.fillMaxSize(),contentScale=ContentScale.Crop)
            else {
                val cfg=LocalConfiguration.current
                val vw=cfg.screenWidthDp.dp
                val d=when{cfg.screenWidthDp<=360->minOf(vw*.78f,272.dp);cfg.screenHeightDp<=780->minOf(vw*.74f,282.dp);else->minOf(vw*.72f,302.dp)}
                // D-pad orange disc = 38% of D-pad minus its 7dp inner padding on both sides.
                // youtube.png has 80px visible alpha inside a 96px canvas, so ×1.2 makes
                // the visible red logo width exactly equal to the orange disc diameter.
                val youtubeCanvas=((d*.38f)-14.dp)*1.2f
                Mdi("youtube",youtubeCanvas,Color(0xFFFF0000))
            }
        }
        Column(Modifier.fillMaxWidth().weight(1f).padding(horizontal=12.dp).padding(bottom=12.dp)){
            PlayerTitleLine(title,7.dp)
            Box(Modifier.fillMaxWidth().height(18.dp),contentAlignment=Alignment.TopCenter){if(se.isNotBlank())Text(se,fontSize=11.sp,fontWeight=FontWeight.Bold,color=Accent,textAlign=androidx.compose.ui.text.style.TextAlign.Center)}
            if(running&&canSeek&&rawDuration>1f&&videoId.isNotBlank()){
                Column(Modifier.fillMaxWidth().offset(y=(-12).dp)){
                    Row(Modifier.fillMaxWidth(),verticalAlignment=Alignment.CenterVertically){
                        Text(fmt(seek.toDouble()),fontSize=11.sp,color=Muted)
                        Spacer(Modifier.weight(1f))
                        if(isLive) Text("● НАЖИВО",fontSize=11.sp,fontWeight=FontWeight.Bold,color=Accent)
                        else Text(fmt(duration.toDouble()),fontSize=11.sp,color=Muted)
                    }
                    SkeletonRange(seek.coerceIn(0f,duration),{seek=it.coerceIn(0f,duration)},{onSeek(seek.coerceIn(0f,duration).toInt())},0f,duration,Modifier.fillMaxWidth())
                }
            }else if(running&&isLive){
                Box(Modifier.fillMaxWidth().height(34.dp).offset(y=(-8).dp),contentAlignment=Alignment.CenterEnd){Text("● НАЖИВО",fontSize=11.sp,fontWeight=FontWeight.Bold,color=Accent)}
            }
            Box(Modifier.weight(1f).fillMaxWidth(),contentAlignment=Alignment.BottomCenter){YoutubePanel(onRemote,onMute,volume==0,youtubeControls)}
            VolumeRow(volume,onVolume,onMute)
        }
    }
}

@Composable private fun VideoRemoteCard(api:HomeApi,p:JSONObject,volume:Int,onVolume:(Int)->Unit,onControl:(String)->Unit,onSeek:(Int)->Unit,onMute:()->Unit){
    val running=p.optBoolean("running")
    val title=if(running)p.optString("display-title",p.optString("media-title","Відео")) else "Нічого не відтворюється"
    val se=if(running&&p.has("season")&&!p.isNull("season")){val s=p.optString("season","").takeUnless{it.equals("null",true)}.orEmpty();val e=if(p.has("episode")&&!p.isNull("episode"))p.optString("episode","").filter{it.isDigit()} else "";if(s.isNotBlank())"S$s"+(if(e.isNotBlank())" · E$e" else "") else ""}else ""
    val poster=p.optString("poster",p.optString("poster_landscape",""))
    val bitmap=remoteBitmap(if(poster.isNotBlank()&&api.server!=null)api.server+poster else null,600,900)
    var seek by remember{mutableFloatStateOf(0f)}
    val serverPos=p.optDouble("time-pos",0.0).toFloat()
    LaunchedEffect(serverPos){seek=serverPos}
    val duration=p.optDouble("duration",0.0).toFloat().coerceAtLeast(1f)
    val scope=rememberCoroutineScope()
    var episodeStepping by remember{mutableStateOf(false)}
    val seriesNav=running&&se.isNotBlank()
    Column(Modifier.fillMaxSize().clip(RoundedCornerShape(16.dp)).background(Card)){
        BoxWithConstraints(Modifier.weight(1f).fillMaxWidth().padding(top=10.dp,start=12.dp,end=12.dp),contentAlignment=Alignment.Center){
            val posterWidth=minOf(maxWidth,maxHeight*(2f/3f))
            val frameSide=((maxWidth-posterWidth)/2f).coerceAtLeast(0.dp)
            Box(Modifier.fillMaxHeight().width(posterWidth).clip(RoundedCornerShape(14.dp)).background(Bg).border(1.dp,Line,RoundedCornerShape(14.dp)),contentAlignment=Alignment.Center){if(bitmap!=null)Image(bitmap,null,Modifier.fillMaxSize(),contentScale=ContentScale.Crop) else Mdi("television-play",48.dp,Color(0xFFD7DCE1))}
            if(seriesNav){
                fun step(direction:Int){if(episodeStepping)return;episodeStepping=true;scope.launch{runCatching{api.post("/api/video/episode/step",JSONObject().put("direction",direction))};episodeStepping=false}}
                Box(Modifier.align(Alignment.CenterStart).width(frameSide).fillMaxHeight().clickable(enabled=!episodeStepping){step(-1)},contentAlignment=Alignment.Center){Text("‹",fontSize=27.sp,lineHeight=28.sp,fontWeight=FontWeight.Medium,color=Color(0xFFF4F7FA))}
                Box(Modifier.align(Alignment.CenterEnd).width(frameSide).fillMaxHeight().clickable(enabled=!episodeStepping){step(1)},contentAlignment=Alignment.Center){Text("›",fontSize=27.sp,lineHeight=28.sp,fontWeight=FontWeight.Medium,color=Color(0xFFF4F7FA))}
            }
        }
        Column(Modifier.fillMaxWidth().padding(horizontal=12.dp).padding(bottom=12.dp)){
            PlayerTitleLine(title,6.dp)
            Box(Modifier.fillMaxWidth().height(18.dp),contentAlignment=Alignment.TopCenter){if(se.isNotBlank())Text(se,fontSize=11.sp,fontWeight=FontWeight.Bold,color=Accent,textAlign=androidx.compose.ui.text.style.TextAlign.Center)}
            Row(Modifier.fillMaxWidth().padding(top=7.dp)){Text(fmt(seek.toDouble()),fontSize=11.sp,color=Muted);Spacer(Modifier.weight(1f));Text(fmt(duration.toDouble()),fontSize=11.sp,color=Muted)}
            SkeletonRange(seek,{seek=it},{onSeek(seek.toInt())},0f,duration,Modifier.fillMaxWidth())
            TransportRow(p.optBoolean("pause"),false,onControl,onMute)
            VolumeRow(volume,onVolume,onMute)
        }
    }
}

@Composable private fun TvRemoteCard(api:HomeApi,active:Boolean,p:JSONObject,volume:Int,onVolume:(Int)->Unit,onControl:(String)->Unit,onMute:()->Unit){
    Column(Modifier.fillMaxSize().clip(RoundedCornerShape(16.dp)).background(Card)){
        TvChannels(api,active,p.optString("channel_id"),Modifier.weight(1f).fillMaxWidth().padding(top=10.dp,start=12.dp,end=12.dp))
        Column(Modifier.fillMaxWidth().padding(horizontal=12.dp).padding(bottom=12.dp)){TransportRow(false,true,onControl,onMute);VolumeRow(volume,onVolume,onMute)}
    }
}


@Composable private fun TransportRow(paused:Boolean,tv:Boolean,onControl:(String)->Unit,onMute:()->Unit){
    if(tv){Row(Modifier.fillMaxWidth().height(48.dp),horizontalArrangement=Arrangement.Center){RoundControl("volume-off",false,onMute)}} else Row(Modifier.fillMaxWidth().height(48.dp),horizontalArrangement=Arrangement.SpaceAround,verticalAlignment=Alignment.CenterVertically){RoundControl("rewind-15",false){onControl("back")};RoundControl(if(paused)"play" else "pause",true){onControl("toggle")};RoundControl("fast-forward-15",false){onControl("forward")};RoundControl("volume-off",false,onMute)}
}
@Composable private fun RoundControl(icon:String,primary:Boolean=false,onClick:()->Unit){Box(Modifier.size(48.dp).clip(CircleShape).background(if(primary)Accent else Color.Transparent).clickable(onClick=onClick),contentAlignment=Alignment.Center){Mdi(icon,if(icon=="volume-off")26.dp else 25.dp,Text)}}

@Composable private fun VolumeRow(volume:Int,onVolume:(Int)->Unit,onMute:()->Unit){var v by remember(volume){mutableFloatStateOf(volume.toFloat())};Row(Modifier.fillMaxWidth().height(49.dp),verticalAlignment=Alignment.Top){Box(Modifier.size(40.dp).offset(y=9.dp).clickable{onVolume((volume-5).coerceAtLeast(0))},contentAlignment=Alignment.Center){Mdi("volume-high",19.dp,Text.copy(alpha=.72f))};BoxWithConstraints(Modifier.weight(1f).height(49.dp)){val lw=36.dp;val x=(maxWidth*(v.coerceIn(0f,100f)/100f)-lw/2).coerceIn(0.dp,(maxWidth-lw).coerceAtLeast(0.dp));Text("${v.toInt()}%",fontSize=11.sp,lineHeight=11.sp,color=Color(0xFFEEF1F3),textAlign=androidx.compose.ui.text.style.TextAlign.Center,modifier=Modifier.offset(x=x).width(lw));SkeletonRange(v,{v=it},{onVolume((v/5f).toInt()*5)},0f,100f,Modifier.fillMaxWidth().offset(y=18.dp))};Box(Modifier.size(40.dp).offset(y=9.dp).clickable{onVolume((volume+5).coerceAtMost(100))},contentAlignment=Alignment.Center){Mdi("volume-high",27.dp,Text)}}}


@Composable private fun YoutubePanel(onRemote:(String)->Unit,onMute:()->Unit,muted:Boolean,controls:JSONObject){
    val config=LocalConfiguration.current
    val viewportWidth=config.screenWidthDp.dp
    BoxWithConstraints(Modifier.fillMaxWidth(),contentAlignment=Alignment.BottomCenter){
        val narrow=config.screenWidthDp<=360
        val compactH=config.screenHeightDp<=780
        val side=if(narrow)46.dp else if(compactH)48.dp else 52.dp
        val d=when{
            narrow->minOf(viewportWidth*.78f,272.dp)
            compactH->minOf(viewportWidth*.74f,282.dp)
            else->minOf(viewportWidth*.72f,302.dp)
        }
        val panelMin=when{narrow->286.dp;compactH->296.dp;else->316.dp}
        Box(Modifier.fillMaxWidth().heightIn(min=panelMin)){
            val likeAvailable=controls.optBoolean("like_available",false)
            val liked=controls.optBoolean("liked",false)
            val ccAvailable=controls.optBoolean("subtitles_available",false)
            val ccEnabled=controls.optBoolean("subtitles_enabled",false)
            // Four satellite keys share one symmetric rectangle around the D-pad.
            // Equal top/bottom insets keep their centers equidistant from the D-pad center.
            val satelliteInset=8.dp
            Dpad(d,Modifier.requiredSize(d).align(Alignment.Center).offset(y=4.dp),onRemote)
            CircleLikeButton(side,liked,likeAvailable,Modifier.align(Alignment.TopStart).offset(y=satelliteInset)){onRemote("like")}
            CircleRemoteActionButton("subtitles",side,ccEnabled,ccAvailable,Modifier.align(Alignment.TopEnd).offset(y=satelliteInset)){onRemote("subtitles")}
            CircleRemoteButton("keyboard-return",side,false,modifier=Modifier.align(Alignment.BottomStart).offset(y=-satelliteInset)){onRemote("back")}
            CircleRemoteButton("volume-off",side,muted,modifier=Modifier.align(Alignment.BottomEnd).offset(y=-satelliteInset),onClick=onMute)
        }
    }
}

@Composable private fun CircleRemoteButton(icon:String,size:Dp,active:Boolean=false,modifier:Modifier=Modifier,onClick:()->Unit){Box(modifier.size(size).clip(CircleShape).background(Color(0xFF20272E)).border(1.dp,Color(0xFF303942),CircleShape).clickable(onClick=onClick),contentAlignment=Alignment.Center){Mdi(icon,if(icon=="volume-off")27.dp else 24.dp,if(active)Accent else Color(0xFFE9EDF0))}}

@Composable private fun CircleRemoteActionButton(icon:String,size:Dp,active:Boolean,enabled:Boolean,modifier:Modifier=Modifier,onClick:()->Unit){Box(modifier.size(size).clip(CircleShape).background(Color(0xFF20272E)).border(1.dp,if(active)Accent else Color(0xFF303942),CircleShape).clickable(enabled=enabled,onClick=onClick),contentAlignment=Alignment.Center){Mdi(icon,24.dp,(if(active)Accent else Color(0xFFE9EDF0)).copy(alpha=if(enabled)1f else .35f))}}

@Composable private fun CircleLikeButton(size:Dp,active:Boolean,enabled:Boolean,modifier:Modifier=Modifier,onClick:()->Unit){Box(modifier.size(size).clip(CircleShape).background(Color(0xFF20272E)).border(1.dp,Color(0xFF303942),CircleShape).clickable(enabled=enabled,onClick=onClick),contentAlignment=Alignment.Center){Mdi("thumb-up-outline",24.dp,(if(active)Accent else Color(0xFFE9EDF0)).copy(alpha=if(enabled)1f else .35f))}}


@Composable private fun Dpad(size:Dp,modifier:Modifier=Modifier,onRemote:(String)->Unit){
    Box(modifier.size(size).shadow(13.dp,CircleShape,ambientColor=Color.Black.copy(alpha=.30f),spotColor=Color.Black.copy(alpha=.30f)).clip(CircleShape).border(1.dp,Color(0xFF3C4650),CircleShape),contentAlignment=Alignment.Center){
        Canvas(Modifier.fillMaxSize()){
            val c=Offset(this.size.width/2,this.size.height/2);val r=min(this.size.width,this.size.height)/2
            drawCircle(brush=Brush.radialGradient(colorStops=arrayOf(0f to Color(0xFF343C44),.25f to Color(0xFF343C44),.26f to Color(0xFF242B32),.59f to Color(0xFF242B32),.60f to Color(0xFF161B20),1f to Color(0xFF161B20)),center=c,radius=r),radius=r,center=c)
        }
        DpadKey("chevron-up",Modifier.align(Alignment.TopCenter).fillMaxWidth(.50f).fillMaxHeight(.32f)){onRemote("up")}
        DpadKey("chevron-down",Modifier.align(Alignment.BottomCenter).fillMaxWidth(.50f).fillMaxHeight(.32f)){onRemote("down")}
        DpadKey("chevron-left",Modifier.align(Alignment.CenterStart).fillMaxWidth(.32f).fillMaxHeight(.50f)){onRemote("left")}
        DpadKey("chevron-right",Modifier.align(Alignment.CenterEnd).fillMaxWidth(.32f).fillMaxHeight(.50f)){onRemote("right")}
        Box(Modifier.fillMaxSize(.38f).clip(CircleShape).background(Color(0xFF20272E)).clickable{onRemote("ok")}.padding(7.dp),contentAlignment=Alignment.Center){Box(Modifier.fillMaxSize().clip(CircleShape).background(Brush.verticalGradient(listOf(Color(0xFFFB771D),Color(0xFFE85800)))),contentAlignment=Alignment.Center){Text("OK",fontSize=15.sp,fontWeight=FontWeight.Bold,color=Color.White)}}
    }
}

@Composable private fun DpadKey(icon:String,modifier:Modifier,onClick:()->Unit){Box(modifier.clickable(onClick=onClick),contentAlignment=Alignment.Center){Mdi(icon,29.dp)}}

@OptIn(ExperimentalFoundationApi::class)
@Composable private fun TvChannels(api:HomeApi,screenActive:Boolean,activeId:String,modifier:Modifier){
    var channels by remember{mutableStateOf(emptyList<JSONObject>())}
    val scope=rememberCoroutineScope()
    val listState=rememberLazyListState()
    LaunchedEffect(api.server,screenActive){
        if(api.server!=null){
            runCatching{api.get("/api/tv/channels")}.onSuccess{channels=it.optJSONArray("channels")?.objects().orEmpty()}
            if(screenActive)while(true){
                delay(30000)
                runCatching{api.get("/api/tv/channels")}.onSuccess{channels=it.optJSONArray("channels")?.objects().orEmpty()}
            }
        }
    }
    LaunchedEffect(activeId,channels.size){
        val idx=channels.indexOfFirst{it.cleanText("channel_id")==activeId}
        if(idx>=0)listState.scrollToItem((idx-3).coerceAtLeast(0))
    }
    BoxWithConstraints(modifier.clip(RoundedCornerShape(13.dp)).background(Card)){
        val rowHeight=(maxHeight/7f).coerceAtLeast(48.dp)
        LazyColumn(
            modifier=Modifier.fillMaxSize(),
            state=listState,
            userScrollEnabled=true
        ){
            items(channels,key={it.cleanText("channel_id",it.cleanText("name"))}){c->
                val active=c.cleanText("channel_id")==activeId
                val name=c.cleanText("display_name",c.cleanText("name","Канал"))
                val prog=c.cleanText("programme_title")
                val picon=c.cleanText("picon")
                val piconUrl=when{picon.startsWith("http")->picon;picon.isNotBlank()&&api.server!=null->api.server+picon;else->null}
                val bm=remoteBitmap(piconUrl,128,128)
                Row(
                    Modifier.fillMaxWidth().height(rowHeight).alpha(if(active)1f else .62f)
                        .background(if(active)AccentSoft else Color.Transparent)
                        .clickable{scope.launch{runCatching{api.post("/api/tv/play",JSONObject().put("channel_id",c.cleanText("channel_id")))}}}
                        .padding(horizontal=10.dp,vertical=4.dp),
                    verticalAlignment=Alignment.CenterVertically
                ){
                    Box(Modifier.size(42.dp),contentAlignment=Alignment.Center){
                        if(bm!=null)Image(bm,null,Modifier.fillMaxSize(),contentScale=ContentScale.Fit)
                        else Mdi("television",27.dp,Muted)
                    }
                    Column(Modifier.weight(1f).padding(start=10.dp),verticalArrangement=Arrangement.Center){
                        if(prog.isBlank())Text(name,fontSize=13.sp,lineHeight=16.sp,fontWeight=FontWeight.SemiBold,color=if(active)Accent else Text,maxLines=1,overflow=TextOverflow.Ellipsis)
                        else{
                            Text(name,fontSize=10.sp,lineHeight=11.sp,fontWeight=FontWeight.SemiBold,color=if(active)Accent else Muted,maxLines=1,overflow=TextOverflow.Ellipsis)
                            Text(prog,fontSize=13.sp,lineHeight=16.sp,fontWeight=FontWeight.Bold,color=if(active)Accent else Text,maxLines=1,softWrap=false,modifier=Modifier.fillMaxWidth().basicMarquee(iterations=Int.MAX_VALUE))
                        }
                    }
                }
            }
        }
    }
}




private fun contentTypeLabel(raw:String,mediaType:String=""):String {
    val labels=mapOf("movie" to "Фільм","series" to "Серіал","anime_movie" to "Аніме-фільм","anime_series" to "Аніме-серіал","animation_movie" to "Мультфільм","animation_series" to "Мультсеріал","documentary_movie" to "Документальний фільм","documentary_series" to "Документальний серіал")
    return labels[raw.lowercase(Locale.ROOT)] ?: when(mediaType.lowercase(Locale.ROOT)){"tv"->"Серіал";"movie"->"Фільм";else->raw}
}
private fun historyKindLabel(x:JSONObject):String=if(x.optString("source_state")=="trailer_only"||x.optBoolean("trailer_only",false))"Трейлер" else contentTypeLabel(x.optString("content_type"),x.optString("media_type"))
private fun sourceVoice(x:JSONObject)=x.optString("translation",x.optString("group",""))
private fun isTrailerSource(x:JSONObject?):Boolean {
    if(x==null)return false
    if(x.optBoolean("is_trailer",false)||x.optString("source_type").equals("trailer",true))return true
    val marker=listOf(
        x.optString("title"),x.optString("page_title"),x.optString("discovery_page_title"),
        x.optString("group"),x.optString("translation"),x.optString("episode"),
        x.optString("source_type"),x.optString("kind")
    ).joinToString(" ").lowercase(Locale.ROOT)
    return Regex("(^|[^a-zа-яіїєґ])(trailer|трейлер(?:и|ів|ом|а)?)([^a-zа-яіїєґ]|$)",RegexOption.IGNORE_CASE).containsMatchIn(marker)
}
private fun sourceValues(a:List<JSONObject>,key:String,season:String?=null,episode:String?=null,quality:String?=null,excludeTrailers:Boolean=false):List<String>{
    return a.filter { (!excludeTrailers || !isTrailerSource(it)) && (season.isNullOrBlank()||it.optString("season")==season) && (episode.isNullOrBlank()||it.optString("episode")==episode) && (quality.isNullOrBlank()||it.optString("quality")==quality) }
        .mapNotNull { val v=if(key=="voice")sourceVoice(it) else it.optString(key,"");v.takeIf(String::isNotBlank) }.distinct().sortedWith(compareBy<String>{Regex("\\d+").find(it)?.value?.toIntOrNull()?:999999}.thenBy{it})
}

private fun trackDisplayLabel(t:JSONObject,index:Int):String{val bits=listOf(t.optString("title"),t.optString("lang"),t.optString("audio-channels",t.optString("demux-channels")),t.optString("codec")).filter{it.isNotBlank()};return if(bits.isNotEmpty())bits.joinToString(" · ") else "Доріжка ${index+1}"}
@Composable private fun SlowOverflowOverview(value:String,modifier:Modifier=Modifier){
    val scroll=rememberScrollState()
    LaunchedEffect(value,scroll.maxValue){
        var idleUntil=0L
        while(true){
            val now=withFrameMillis{it}
            val maxOffset=scroll.maxValue
            if(maxOffset<=0){
                if(scroll.value!=0)scroll.scrollTo(0)
                delay(160)
                continue
            }
            if(scroll.isScrollInProgress){
                idleUntil=now+1600L
                delay(80)
                continue
            }
            if(now<idleUntil){delay(80);continue}
            if(scroll.value>=maxOffset){
                delay(2200)
                scroll.scrollTo(0)
                delay(1400)
                continue
            }
            scroll.scrollTo((scroll.value+1).coerceAtMost(maxOffset))
            delay(40)
        }
    }
    Box(modifier){
        Text(value,fontSize=11.sp,lineHeight=15.sp,color=Muted,modifier=Modifier.fillMaxWidth().verticalScroll(scroll))
    }
}

@Composable private fun HistoryPickerField(value:String,enabled:Boolean,onClick:()->Unit){Row(Modifier.fillMaxWidth().heightIn(min=56.dp),verticalAlignment=Alignment.Bottom){Box(Modifier.width(28.dp).height(39.dp).padding(bottom=9.dp),contentAlignment=Alignment.BottomCenter){Mdi("history",20.dp,Color(0xFFD8DDE2))};Spacer(Modifier.width(8.dp));Column(Modifier.weight(1f)){Text(ui("Історія"),fontSize=10.sp,color=Color(0xFFC4CAD0),modifier=Modifier.padding(bottom=3.dp));Row(Modifier.fillMaxWidth().height(39.dp).clip(RoundedCornerShape(9.dp)).background(Input).border(1.dp,Line,RoundedCornerShape(9.dp)).clickable(enabled=enabled,onClick=onClick).padding(start=10.dp,end=9.dp),verticalAlignment=Alignment.CenterVertically){Text(value,fontSize=14.sp,color=if(enabled)Text else Text.copy(alpha=.55f),modifier=Modifier.weight(1f),maxLines=1,overflow=TextOverflow.Ellipsis);Mdi("chevron-down",16.dp,Muted.copy(alpha=if(enabled)1f else .55f))}}}}
@Composable private fun HistoryPickerDialog(api:HomeApi,items:List<JSONObject>,onSelect:(JSONObject)->Unit,onDismiss:()->Unit){
    var query by remember{mutableStateOf("")};val q=query.trim().lowercase(Locale.ROOT);val visible=if(q.isBlank())items else items.filter{x->listOf(x.optString("title"),x.optString("year"),x.optString("content_type"),x.optString("overview")).joinToString(" ").lowercase(Locale.ROOT).contains(q)}
    val focusRequester=remember{FocusRequester()};val keyboard=LocalSoftwareKeyboardController.current
    Dialog(onDismissRequest=onDismiss,properties=DialogProperties(usePlatformDefaultWidth=false)){
        Box(Modifier.fillMaxSize().background(Color.Black.copy(alpha=.72f)),contentAlignment=Alignment.BottomCenter){Box(Modifier.matchParentSize().clickable(onClick=onDismiss));Column(Modifier.fillMaxWidth().widthIn(max=500.dp).fillMaxHeight(.92f).clip(RoundedCornerShape(topStart=18.dp,topEnd=18.dp)).background(Input).border(1.dp,Line,RoundedCornerShape(topStart=18.dp,topEnd=18.dp)).clickable(enabled=false){}){
            Row(Modifier.fillMaxWidth().height(52.dp).padding(horizontal=15.dp),verticalAlignment=Alignment.CenterVertically){Text(ui("Історія"),fontSize=17.sp,fontWeight=FontWeight.Bold,color=Text,modifier=Modifier.weight(1f));Mdi("close-circle-outline",24.dp,Muted,Modifier.clickable(onClick=onDismiss))}
            Column(Modifier.fillMaxWidth().padding(horizontal=12.dp,vertical=10.dp)){
                Row(Modifier.fillMaxWidth().height(42.dp).clip(RoundedCornerShape(12.dp)).background(Color(0xFF0B1014)).border(1.dp,Color(0xFF353C42),RoundedCornerShape(12.dp)).clickable{focusRequester.requestFocus();keyboard?.show()}.padding(horizontal=11.dp),verticalAlignment=Alignment.CenterVertically){Mdi("magnify",20.dp,Muted);Spacer(Modifier.width(8.dp));androidx.compose.foundation.text.BasicTextField(value=query,onValueChange={query=it},singleLine=true,textStyle=androidx.compose.ui.text.TextStyle(color=Text,fontSize=14.sp),modifier=Modifier.weight(1f).focusRequester(focusRequester),decorationBox={inner->Box(Modifier.fillMaxWidth()){if(query.isEmpty())Text(ui("Пошук в історії"),color=Color(0xFF747D85),fontSize=14.sp);inner()}})}
                Row(Modifier.fillMaxWidth().padding(horizontal=2.dp,vertical=6.dp)){Text(ui("Останні додані або переглянуті — першими"),fontSize=10.sp,color=Muted);Spacer(Modifier.weight(1f));Text(if(q.isBlank())"${items.size}" else "${visible.size} з ${items.size}",fontSize=10.sp,fontWeight=FontWeight.Bold,color=Accent)}
            }
            LazyColumn(Modifier.weight(1f).padding(horizontal=10.dp),contentPadding=PaddingValues(bottom=18.dp)){items(visible,key={it.optString("history_id",it.optString("title"))}){item->HistoryPickerRow(api,item){onSelect(item)}}}
        }}
    }
}
@Composable private fun HistoryPickerRow(api:HomeApi,item:JSONObject,onClick:()->Unit){val poster=item.optString("poster");val bm=remoteBitmap(if(poster.isBlank())null else if(poster.startsWith("http"))poster else api.server+poster,192,288);Row(Modifier.fillMaxWidth().heightIn(min=112.dp).clickable(onClick=onClick).padding(horizontal=3.dp,vertical=9.dp),verticalAlignment=Alignment.CenterVertically){Box(Modifier.width(64.dp).height(96.dp).clip(RoundedCornerShape(8.dp)).background(Bg),contentAlignment=Alignment.Center){if(bm!=null)Image(bm,null,Modifier.fillMaxSize(),contentScale=ContentScale.Crop)else Mdi("movie-open",30.dp,Muted)};Column(Modifier.weight(1f).padding(start=11.dp)){Text(item.optString("title","Твір"),fontSize=15.sp,lineHeight=19.sp,fontWeight=FontWeight.SemiBold,color=Text,maxLines=2,overflow=TextOverflow.Ellipsis);val meta=listOf(historyKindLabel(item),item.optString("year"),historyRuntimeLabel(item)).filter{it.isNotBlank()}.joinToString(" · ");if(meta.isNotBlank())Text(meta,fontSize=10.sp,fontWeight=FontWeight.Bold,color=Accent,modifier=Modifier.padding(top=4.dp));val ov=item.optString("overview");if(ov.isNotBlank())Text(ov,fontSize=11.sp,lineHeight=15.sp,color=Muted,maxLines=3,overflow=TextOverflow.Ellipsis,modifier=Modifier.padding(top=7.dp))}}}
private const val TRAKT_PACKAGE = "tv.trakt.trakt"
private const val MOVIEBASE_PACKAGE = "com.moviebase"


@Composable
private fun VideoScreen(api:HomeApi,active:Boolean,selectionRevision:Int,hyperion:Boolean,onHyperion:(Boolean)->Unit,outputTarget:String,onOutputTarget:(String)->Unit,onMenu:()->Unit,onApplied:()->Unit){
    val scope=rememberCoroutineScope(); val context=LocalContext.current
    var history by remember{mutableStateOf(emptyList<JSONObject>())}; var historyOpen by remember{mutableStateOf(false)}; var seasonOpen by remember{mutableStateOf(false)}; var detailOpen by remember{mutableStateOf(false)}; var loadingSeason by remember{mutableStateOf("")}
    var selection by remember{mutableStateOf(JSONObject())}; var job by remember{mutableStateOf<JSONObject?>(null)}; var sources by remember{mutableStateOf(emptyList<JSONObject>())}; var jobId by remember{mutableStateOf("")}; var loading by remember{mutableStateOf(false)}
    var season by remember{mutableStateOf("")}; var episode by remember{mutableStateOf("")}; var quality by remember{mutableStateOf("")}; var voice by remember{mutableStateOf("")}; var autoplay by remember{mutableStateOf(false)}; var monitor by remember{mutableStateOf(false)}; var monitorReady by remember{mutableStateOf(false)}; var external by remember{mutableStateOf(JSONObject())}; var message by remember{mutableStateOf("")}; var currentPlayer by remember{mutableStateOf(JSONObject())}; var audioSel by remember{mutableStateOf("")}; var subSel by remember{mutableStateOf("")}
    suspend fun loadJob(id:String){
        if(id.isBlank())return; loading=true
        repeat(30){
            val j=runCatching{api.get("/api/jobs/$id")}.getOrNull()?:return@repeat
            val st=j.optString("status")
            if(st !in setOf("resolving","discovering","identified","cached_mapping_checked","searching_sources","candidates_found")){
                job=j;sources=j.optJSONArray("sources")?.objects().orEmpty();loading=false
                if(st=="no_verified_sources"&&sources.isEmpty())message="Перевірені джерела не знайдено" else if(message=="Перевірені джерела не знайдено")message=""
                return
            }
            delay(500)
        }
        loading=false
    }
    suspend fun loadMonitor(){
        if(api.server==null||jobId.isBlank()){monitor=false;monitorReady=false;return}
        monitorReady=false
        runCatching{api.get("/api/video/monitor?job_id=${Uri.encode(jobId)}")}.onSuccess{monitor=it.optBoolean("enabled");monitorReady=true}.onFailure{monitor=false;monitorReady=false}
    }
    suspend fun loadAll():Boolean{
        if(api.server==null)return false
        // Fast path first: render the selected work immediately. History is the expensive call.
        val sel=runCatching{api.get("/api/video/selection")};sel.onSuccess{selection=it;jobId=it.optString("job_id")}
        runCatching{api.get("/api/player")}.onSuccess{currentPlayer=it}
        if(sel.isSuccess&&jobId.isNotBlank()){loadJob(jobId);loadMonitor()}
        val h=runCatching{api.get("/api/video/history")};h.onSuccess{history=it.optJSONArray("items")?.objects().orEmpty()}
        runCatching{api.get("/api/video/autoplay")}.onSuccess{autoplay=it.optBoolean("enabled")}
        return h.isSuccess&&sel.isSuccess
    }
    LaunchedEffect(api.server,selectionRevision){
        if(api.server!=null){
            while(true){if(loadAll())break;delay(1500)}
        }
    }
    LaunchedEffect(api.server,jobId,active){if(api.server!=null){runCatching{api.get("/api/player")}.onSuccess{currentPlayer=it};if(active)while(true){delay(2500);runCatching{api.get("/api/player")}.onSuccess{currentPlayer=it}}}}
    LaunchedEffect(api.server,jobId){if(jobId.isNotBlank())loadMonitor() else {monitor=false;monitorReady=false}}
    val cat=job?.optJSONObject("catalog")?:JSONObject()
    val isSeries=cat.optString("media_type",job?.optString("history_media_type","")?:"").lowercase(Locale.ROOT)=="tv"
    LaunchedEffect(job,sources,isSeries){
        val ss=selection; season=if(isSeries)ss.optString("season") else ""; episode=if(isSeries)ss.optString("episode") else ""; voice=ss.optString("voice")
        val sid=ss.optString("source_id")
        if(sid.isNotBlank())sources.firstOrNull{it.optString("source_id")==sid}?.let{quality=it.optString("quality");if(isSeries&&season.isBlank())season=it.optString("season");if(isSeries&&episode.isBlank())episode=it.optString("episode");if(voice.isBlank())voice=sourceVoice(it)}
        if(!isSeries){season="";episode=""}
        if(jobId.isNotBlank())external=runCatching{api.get("/api/video/external-links?job_id=${Uri.encode(jobId)}")}.getOrElse{JSONObject()}
    }
    val seasonItems=if(isSeries)cat.optJSONArray("seasons")?.objects().orEmpty() else emptyList()
    val loadedSeasons=if(isSeries)(sourceValues(sources,"season",excludeTrailers=true)+seasonItems.filter{it.optBoolean("loaded")||it.optString("load_status")=="loaded"}.map{it.optInt("season_number").toString()}).filter{it.isNotBlank()&&it!="0"}.distinct().sortedBy{it.toIntOrNull()?:9999} else emptyList()
    val tmdbSeasons=seasonItems.filter{it.optBoolean("released",true)}.mapNotNull{it.optInt("season_number",0).takeIf{n->n>0}?.toString()}
    val seasons=(loadedSeasons+tmdbSeasons).distinct().sortedBy{it.toIntOrNull()?:9999}
    if(isSeries&&season.isBlank()&&loadedSeasons.isNotEmpty())season=loadedSeasons.first()
    val seasonLoaded=season.isNotBlank()&&season in loadedSeasons
    val episodes=if(isSeries)sourceValues(sources,"episode",season,excludeTrailers=true) else emptyList()
    if(isSeries&&episode !in episodes)episode=episodes.firstOrNull().orEmpty()
    val qualities=if(isSeries)sourceValues(sources,"quality",season,episode,excludeTrailers=true) else sourceValues(sources,"quality")
    if(quality !in qualities)quality=qualities.firstOrNull().orEmpty()
    val voices=if(isSeries)sourceValues(sources,"voice",season,episode,quality,excludeTrailers=true) else sourceValues(sources,"voice",quality=quality)
    if(voice !in voices)voice=voices.firstOrNull().orEmpty()
    val poster=if(isSeries)cat.optJSONObject("season_posters")?.optString(season,cat.optString("poster",""))?:cat.optString("poster","") else cat.optString("poster","")
    val bm=remoteBitmap(if(poster.isNotBlank()&&api.server!=null)api.server+poster else null,256,384)
    val selectedCandidate=if(isSeries)
        sources.firstOrNull{it.optString("season")==season&&it.optString("episode")==episode&&it.optString("quality")==quality&&sourceVoice(it)==voice}
    else
        sources.firstOrNull{it.optString("quality")==quality&&sourceVoice(it)==voice}
    val selectedOrTrailer=selectedCandidate ?: sources.firstOrNull{isTrailerSource(it)&&it.optString("quality")==quality&&sourceVoice(it)==voice}
    val selectedIsTrailer=isTrailerSource(selectedOrTrailer)
    val selectedDuration=mediaDurationLabel(selectedOrTrailer?.optDouble("duration",0.0)?:0.0).ifBlank{metadataRuntimeLabel(cat.optJSONObject("details")?:JSONObject())}
    val activeSame=selectedOrTrailer!=null&&currentPlayer.optBoolean("running")&&currentPlayer.optString("job_id")==jobId&&currentPlayer.optString("source_id")==selectedOrTrailer.optString("source_id")
    val audioTracks=if(activeSame)currentPlayer.optJSONArray("audio_tracks")?.objects().orEmpty() else emptyList(); val subTracks=if(activeSame)currentPlayer.optJSONArray("subtitle_tracks")?.objects().orEmpty() else emptyList()
    val audioLabels=audioTracks.mapIndexed{i,t->trackDisplayLabel(t,i)}; val subTrackLabels=subTracks.mapIndexed{i,t->trackDisplayLabel(t,i)}; val subLabels=if(activeSame)listOf("Вимкнено")+subTrackLabels else emptyList()
    LaunchedEffect(currentPlayer,activeSame){
        if(activeSame){
            val aid=currentPlayer.opt("aid")?.toString(); val ai=audioTracks.indexOfFirst{it.opt("id")?.toString()==aid}; audioSel=if(ai>=0)audioLabels[ai] else ""
            val sid=currentPlayer.opt("sid"); if(sid==null||sid==JSONObject.NULL||sid==false)subSel="Вимкнено" else {val si=subTracks.indexOfFirst{it.opt("id")?.toString()==sid.toString()};subSel=if(si>=0)subTrackLabels[si] else ""}
        }
    }
    Box(Modifier.fillMaxSize()){
        AmbientPosterBackground(bm,Modifier.fillMaxSize())
        Column(Modifier.fillMaxSize()){
        Header(ui("Відео"),api.server!=null,hyperion,onHyperion,outputTarget,onOutputTarget,onMenu,ambientColor=remember(bm){ambientComplement(bm)}.takeIf{bm!=null})
        LazyColumn(Modifier.weight(1f).padding(horizontal=13.dp),contentPadding=PaddingValues(top=4.dp,bottom=18.dp)){
            item{
                Column(Modifier.fillMaxWidth().clip(RoundedCornerShape(16.dp)).background(Card).border(1.dp,Line,RoundedCornerShape(16.dp)).padding(start=12.dp,end=12.dp,top=14.dp,bottom=12.dp)){
                    Row(Modifier.fillMaxWidth().height(163.dp).clip(RoundedCornerShape(13.dp)).background(Input).border(1.dp,Line,RoundedCornerShape(13.dp)).padding(11.dp),verticalAlignment=Alignment.CenterVertically){
                        Box(Modifier.width(94.dp).height(141.dp).clip(RoundedCornerShape(10.dp)).background(Bg).clickable(enabled=bm!=null){detailOpen=true},contentAlignment=Alignment.Center){
                            if(bm!=null)Image(bm,null,Modifier.fillMaxSize(),contentScale=ContentScale.Crop) else Mdi("movie-open",28.dp,Muted.copy(alpha=.35f))
                        }
                        Column(Modifier.weight(1f).fillMaxHeight().padding(start=18.dp),verticalArrangement=Arrangement.Center){
                            Row(Modifier.fillMaxWidth(),verticalAlignment=Alignment.CenterVertically){Text(cat.optString("title",job?.optString("title","Відео")?:"Відео"),fontSize=15.sp,lineHeight=18.sp,fontWeight=FontWeight.Bold,color=Text,maxLines=2,overflow=TextOverflow.Ellipsis,modifier=Modifier.weight(1f));if(!isSeries&&selectedDuration.isNotBlank())Text(selectedDuration,fontSize=11.sp,fontWeight=FontWeight.SemiBold,color=Muted,modifier=Modifier.padding(start=8.dp))}
                            val displayedKind=if(isTrailerSource(selectedOrTrailer))"Трейлер" else contentTypeLabel(cat.optString("content_type"),cat.optString("media_type",job?.optString("history_media_type","")?:""))
                            val releasedSeasonItems=seasonItems.filter{it.optBoolean("released",true)}
                            val totalEpisodes=releasedSeasonItems.sumOf{it.optInt("episode_count",0)}
                            val workMeta=buildList{cat.optString("year").takeIf{it.isNotBlank()}?.let(::add);if(isSeries&&releasedSeasonItems.isNotEmpty())add("${releasedSeasonItems.size} ${if(releasedSeasonItems.size==1)"сезон" else "сезонів"}");if(isSeries&&totalEpisodes>0)add("$totalEpisodes серій");if(!isSeries&&displayedKind.isNotBlank())add(displayedKind)}
                            Row(Modifier.fillMaxWidth().padding(top=5.dp),verticalAlignment=Alignment.CenterVertically){Text(uiDynamic(workMeta.joinToString(" · ")),fontSize=12.sp,lineHeight=17.sp,color=Muted,modifier=Modifier.weight(1f),maxLines=2,overflow=TextOverflow.Ellipsis)}
                            val tmdbRating=cat.optDouble("tmdb_rating_percent",cat.optDouble("tmdb_rating",cat.optDouble("vote_average",0.0)*10)).takeIf{it>0}
                            val imdbRating=cat.optDouble("imdb_rating",0.0).takeIf{it>0}
                            val traktRating=cat.optDouble("trakt_rating",0.0).takeIf{it>0}
                            val moviebaseRating=cat.optDouble("moviebase_rating",0.0).takeIf{it>0}
                            CompactWorkRatingsRow(tmdbRating,imdbRating,traktRating,moviebaseRating,Modifier.fillMaxWidth().padding(top=5.dp))
                        }
                    }
                    val workOverview=cat.optString("overview").trim()
                    if(workOverview.isNotBlank()){
                        Text(workOverview,fontSize=12.sp,lineHeight=17.sp,color=Muted,maxLines=3,overflow=TextOverflow.Ellipsis,modifier=Modifier.fillMaxWidth().padding(top=11.dp).clip(RoundedCornerShape(8.dp)).clickable{detailOpen=true}.padding(horizontal=2.dp,vertical=2.dp))
                        Row(Modifier.fillMaxWidth().clickable{detailOpen=true}.padding(top=4.dp,bottom=2.dp),horizontalArrangement=Arrangement.End,verticalAlignment=Alignment.CenterVertically){Text("Детальніше",fontSize=11.sp,fontWeight=FontWeight.SemiBold,color=Accent);Spacer(Modifier.width(3.dp));Mdi("chevron-right",16.dp,Accent)}
                    }
                    Spacer(Modifier.height(16.dp))
                    HistoryPickerField(if(job!=null)cat.optString("title","Обраний твір")else if(history.isEmpty())"Історія порожня" else "Оберіть збережений твір",history.isNotEmpty()){historyOpen=true}
                    if(isSeries){
                        SeasonPickerField(season,seasonItems,loadedSeasons){seasonOpen=true}
                        SelectorField(ui("Серія"),"play",if(selectedIsTrailer)"Трейлер" else episode,if(selectedIsTrailer)listOf("Трейлер") else episodes,showCount=!selectedIsTrailer,trailing=if(selectedIsTrailer)"" else selectedDuration){if(!selectedIsTrailer)episode=it}
                    }
                    SelectorField(ui("Якість"),"quality-high",quality,qualities){quality=it}
                    SelectorField(if(selectedIsTrailer)"Джерело" else "Озвучка","account-voice",voice,voices){voice=it}
                    if(audioTracks.size>1) SelectorField(ui("Аудіодоріжка"),"volume-high",audioSel,audioLabels){audioSel=it}
                    SelectorField(ui("Субтитри"),"subtitles",if(selectedOrTrailer!=null)subSel else "",if(selectedOrTrailer!=null)subLabels else emptyList()){subSel=it}
                    Spacer(Modifier.height(14.dp))
                    Row(Modifier.fillMaxWidth(),verticalAlignment=Alignment.Bottom,horizontalArrangement=Arrangement.SpaceEvenly){
                        Column(Modifier.width(56.dp),horizontalAlignment=Alignment.CenterHorizontally){
                            RoundAction("play",true){scope.launch{
                            val src=selectedOrTrailer;if(src==null){message="Оберіть варіант";return@launch}
                            if(activeSame){
                                if(audioTracks.size>1&&audioSel.isNotBlank()){val i=audioLabels.indexOf(audioSel);audioTracks.getOrNull(i)?.let{api.post("/api/player/track/audio",JSONObject().put("id",it.opt("id")))}}
                                if(subSel.isNotBlank()){val id=if(subSel=="Вимкнено")"off" else subTracks.getOrNull(subTrackLabels.indexOf(subSel))?.opt("id");if(id!=null)api.post("/api/player/track/subtitle",JSONObject().put("id",id))}
                                val selBody=JSONObject().put("job_id",jobId).put("locked",true).put("voice",voice).put("source_id",src.optString("source_id")).put("title",cat.optString("title"))
                                if(isSeries)selBody.put("season",season).put("episode",episode)
                                api.put("/api/video/selection",selBody);message="Відтворення змінено";onApplied()
                            }else{
                                val o=JSONObject().put("job_id",jobId).put("source_id",src.optString("source_id")).put("voice",voice).put("quality",quality).put("subtitles","off")
                                if(isSeries)o.put("season",season).put("episode",episode)
                                runCatching{api.post("/api/play",o)}.onSuccess{
                                    val selBody=JSONObject().put("job_id",jobId).put("locked",true).put("voice",voice).put("source_id",src.optString("source_id")).put("title",cat.optString("title"))
                                    if(isSeries)selBody.put("season",season).put("episode",episode)
                                    api.put("/api/video/selection",selBody);message="Застосовано";onApplied()
                                }.onFailure{message=it.message?:"Не вдалося застосувати"}
                            }
                        }}
                        }
                        RoundAction("history",false){scope.launch{
                            if(jobId.isBlank()){message="Оберіть твір";return@launch}
                            message="Відновлюю збережений перегляд…"
                            runCatching{api.post("/api/video/resume",JSONObject().put("job_id",jobId))}.onSuccess{out->
                                selection=out.optJSONObject("preferred")?:JSONObject();jobId=out.optString("job_id");selection.put("job_id",jobId);loadJob(jobId);message="Продовжено зі збереженого місця";onApplied()
                            }.onFailure{message=it.message?:"Немає збереженого моменту"}
                        }}
                        val monitorAvailable=jobId.isNotBlank()&&cat.optInt("tmdb_id")>0
                        VideoToggleControl("radar",monitor,monitorAvailable&&monitorReady){v->
                            val previous=monitor;monitor=v;monitorReady=false
                            scope.launch{runCatching{api.put("/api/video/monitor",JSONObject().put("job_id",jobId).put("enabled",v))}.onSuccess{monitor=it.optBoolean("enabled");monitorReady=true;message=if(monitor)"Моніторинг майбутніх релізів увімкнено" else "Моніторинг майбутніх релізів вимкнено"}.onFailure{monitor=previous;monitorReady=true;message=it.message?:"Не вдалося змінити моніторинг"}}
                        }
                        VideoToggleControl("autoplay-repeat",autoplay,isSeries){v->if(isSeries){autoplay=v;scope.launch{runCatching{api.put("/api/video/autoplay",JSONObject().put("enabled",v))}}}}
                    }
                    if(loading||message.isNotBlank())Text(if(loading)"Шукаю перевірені джерела…" else message,fontSize=11.sp,color=if(message.startsWith("Не"))Color(0xFFFF8A80) else Muted,modifier=Modifier.fillMaxWidth().padding(top=8.dp,start=3.dp))
                }
            }
        }
    }
    if(historyOpen)HistoryPickerDialog(api,history,{item->historyOpen=false;scope.launch{val out=runCatching{api.post("/api/video/history/open",JSONObject().put("history_id",item.optString("history_id")))}.getOrNull()?:return@launch;selection=out.optJSONObject("preferred")?:JSONObject();jobId=out.optString("job_id");selection.put("job_id",jobId);loadJob(jobId)}},{historyOpen=false})
    if(seasonOpen)SeasonPickerDialog(api,cat,seasonItems,loadedSeasons,season,loadingSeason,{snum,load->
        season=snum
        if(load){
            loadingSeason=snum
            scope.launch{
                loading=true;message="Шукаю сезон $snum…"
                try{
                    val r=withTimeoutOrNull(45000){api.post("/api/video/season/load",JSONObject().put("job_id",jobId).put("season",snum))}
                    if(r==null){
                        message="Пошук сезону $snum триває надто довго. Спробуйте ще раз"
                    }else{
                        val status=r.optString("status")
                        if(status=="loaded"||status=="already_loaded"){
                            val nid=r.optString("job_id",jobId);jobId=nid;selection.put("job_id",nid)
                            withTimeoutOrNull(15000){loadJob(nid)}
                            message="Сезон $snum додано"
                        }else{
                            withTimeoutOrNull(15000){loadJob(jobId)}
                            message="Сезон $snum поки не знайдено"
                        }
                    }
                }catch(t:Throwable){
                    message=t.message?:"Не вдалося знайти сезон"
                }finally{
                    loading=false;loadingSeason=""
                }
            }
        } else seasonOpen=false
    },{seasonOpen=false})
    if(detailOpen)VideoDetailDialog(context,cat,bm,external,onDismiss={detailOpen=false})
    }
}


@Composable private fun CompactRatingCell(iconRes:Int,label:String,value:String,modifier:Modifier=Modifier){
    Row(modifier.height(46.dp).padding(start=0.dp),verticalAlignment=Alignment.CenterVertically,horizontalArrangement=Arrangement.Start){
        Image(painterResource(iconRes),label,Modifier.size(35.dp).clip(RoundedCornerShape(6.dp)),contentScale=ContentScale.Fit)
        Spacer(Modifier.width(8.dp))
        Text(value,fontSize=14.sp,fontWeight=FontWeight.Bold,color=Text)
    }
}
@Composable private fun CompactWorkRatingsRow(tmdb:Double?,imdb:Double?,trakt:Double?,moviebase:Double?,modifier:Modifier=Modifier){
    Column(modifier.fillMaxWidth()){
        Row(Modifier.fillMaxWidth()){CompactRatingCell(R.drawable.trakt_brand,"Trakt",trakt?.let{"${"%.0f".format(Locale.US,if(it<=10)it*10 else it)}%"}?:"—",Modifier.weight(1f));CompactRatingCell(R.drawable.imdb_brand,"IMDb",imdb?.let{"%.1f".format(Locale.US,it)}?:"—",Modifier.weight(1f))}
        Row(Modifier.fillMaxWidth()){CompactRatingCell(R.drawable.tmdb_brand,"TMDB",tmdb?.let{"${"%.0f".format(Locale.US,it)}%"}?:"—",Modifier.weight(1f));CompactRatingCell(R.drawable.moviebase_brand,"Moviebase",moviebase?.let{"${"%.0f".format(Locale.US,if(it<=10)it*10 else it)}%"}?:"—",Modifier.weight(1f))}
    }
}

@Composable private fun WorkRatingsRow(tmdb:Double?,imdb:Double?,mdblist:Double?,trakt:Double?,moviebase:Double?,modifier:Modifier=Modifier){
    Row(modifier.fillMaxWidth(),horizontalArrangement=Arrangement.SpaceBetween,verticalAlignment=Alignment.CenterVertically){
        Text(tmdb?.let{"${"%.0f".format(Locale.US,it)}%"}?:"—",fontSize=12.sp,fontWeight=FontWeight.SemiBold,color=Text)
        Text(imdb?.let{"%.1f".format(Locale.US,it)}?:"—",fontSize=12.sp,fontWeight=FontWeight.SemiBold,color=Text)
        Text(mdblist?.let{"${"%.0f".format(Locale.US,if(it<=10)it*10 else it)}%"}?:"—",fontSize=12.sp,fontWeight=FontWeight.SemiBold,color=Text)
        Text(trakt?.let{"${"%.0f".format(Locale.US,if(it<=10)it*10 else it)}%"}?:"—",fontSize=12.sp,fontWeight=FontWeight.SemiBold,color=Text)
        Text(moviebase?.let{"${"%.0f".format(Locale.US,if(it<=10)it*10 else it)}%"}?:"—",fontSize=12.sp,fontWeight=FontWeight.SemiBold,color=Text)
    }
}

@Composable private fun SeasonPickerField(value:String,items:List<JSONObject>,loaded:List<String>,onClick:()->Unit){
    val item=items.firstOrNull{it.optInt("season_number").toString()==value}
    val loadedNow=value in loaded
    Row(Modifier.fillMaxWidth().heightIn(min=56.dp),verticalAlignment=Alignment.Bottom){
        Box(Modifier.width(28.dp).height(39.dp).padding(bottom=9.dp),contentAlignment=Alignment.BottomCenter){Mdi("playlist-play",20.dp,Color(0xFFD8DDE2))};Spacer(Modifier.width(8.dp))
        Column(Modifier.weight(1f)){Text(ui("Сезон"),fontSize=10.sp,color=Color(0xFFC4CAD0),modifier=Modifier.padding(bottom=3.dp));Row(Modifier.fillMaxWidth().height(45.dp).clip(RoundedCornerShape(9.dp)).background(Input).border(1.dp,Line,RoundedCornerShape(9.dp)).clickable(enabled=items.isNotEmpty(),onClick=onClick).padding(horizontal=10.dp),verticalAlignment=Alignment.CenterVertically){Text(if(value.isBlank())"Оберіть сезон" else "Сезон $value",fontSize=14.sp,color=Text,modifier=Modifier.weight(1f));val itemStatus=item?.optString("load_status").orEmpty();val state=if(loadedNow)"Завантажено" else if(itemStatus=="not_found")"Не знайдено" else if(item?.optBoolean("released",true)==true)"Доступний" else "Очікується";Text(state,fontSize=10.sp,color=if(loadedNow)Green else if(itemStatus=="not_found")Color(0xFFFF8A80) else Muted,modifier=Modifier.padding(end=8.dp));Mdi("chevron-down",16.dp,Muted)}}
    }
}

@Composable private fun SeasonPickerDialog(api:HomeApi,cat:JSONObject,items:List<JSONObject>,loaded:List<String>,selected:String,loadingSeason:String,onSelect:(String,Boolean)->Unit,onDismiss:()->Unit){
    Dialog(onDismissRequest=onDismiss,properties=DialogProperties(usePlatformDefaultWidth=false)){Box(Modifier.fillMaxSize().background(Color.Black.copy(alpha=.78f)),contentAlignment=Alignment.BottomCenter){Box(Modifier.matchParentSize().clickable(onClick=onDismiss));Column(Modifier.fillMaxWidth().widthIn(max=520.dp).fillMaxHeight(.92f).clip(RoundedCornerShape(topStart=20.dp,topEnd=20.dp)).background(Input).border(1.dp,Line,RoundedCornerShape(topStart=20.dp,topEnd=20.dp)).clickable(enabled=false){}){
        Row(Modifier.fillMaxWidth().height(56.dp).padding(horizontal=16.dp),verticalAlignment=Alignment.CenterVertically){Text(ui("Вибір сезону"),fontSize=18.sp,fontWeight=FontWeight.Bold,color=Text,modifier=Modifier.weight(1f));Mdi("close-circle-outline",24.dp,Muted,Modifier.clickable(onClick=onDismiss))}
        LazyColumn(Modifier.weight(1f).padding(horizontal=10.dp),contentPadding=PaddingValues(bottom=18.dp)){items(items,key={it.optInt("season_number")}){it->val n=it.optInt("season_number");val ns=n.toString();val loadedNow=ns in loaded||it.optBoolean("loaded");val isLoading=loadingSeason==ns;val released=it.optBoolean("released",true);val p=it.optString("poster",cat.optString("poster"));val bm=remoteBitmap(if(p.isBlank())null else if(p.startsWith("http"))p else api.server+p,160,240);Row(Modifier.fillMaxWidth().heightIn(min=118.dp).padding(vertical=5.dp).clip(RoundedCornerShape(12.dp)).background(if(ns==selected)AccentSoft else Card).border(1.dp,if(ns==selected)Accent else Line,RoundedCornerShape(12.dp)).padding(9.dp),verticalAlignment=Alignment.CenterVertically){Row(Modifier.weight(1f).clickable(enabled=!isLoading){onSelect(ns,false)},verticalAlignment=Alignment.CenterVertically){Box(Modifier.width(62.dp).height(93.dp).clip(RoundedCornerShape(8.dp)).background(Bg),contentAlignment=Alignment.Center){if(bm!=null)Image(bm,null,Modifier.fillMaxSize(),contentScale=ContentScale.Crop)else Mdi("movie-open",28.dp,Muted)};Column(Modifier.weight(1f).padding(horizontal=11.dp)){Text(ui("Сезон $n"),fontSize=15.sp,fontWeight=FontWeight.SemiBold,color=Text);val meta=listOf(it.optInt("episode_count").takeIf{x->x>0}?.let{x->"$x серій"},it.optInt("year").takeIf{x->x>0}?.toString()).filterNotNull().joinToString(" · ");if(meta.isNotBlank())Text(meta,fontSize=11.sp,color=Muted,modifier=Modifier.padding(top=4.dp));val loadStatus=it.optString("load_status");Text(if(isLoading)"Завантажується…" else if(loadedNow)"Завантажено" else if(loadStatus=="not_found")"Не знайдено" else if(released)"Не завантажено" else "Ще не вийшов",fontSize=10.sp,color=if(loadedNow)Green else if(isLoading)Accent else if(loadStatus=="not_found")Color(0xFFFF8A80) else Muted,modifier=Modifier.padding(top=6.dp))}};when{
    loadedNow->Mdi("check-circle-outline",27.dp,Green)
    released->Row(verticalAlignment=Alignment.CenterVertically,horizontalArrangement=Arrangement.spacedBy(8.dp)){
        if(isLoading)CircularProgressIndicator(Modifier.size(22.dp),strokeWidth=2.5.dp,color=Accent)
        Mdi("download",26.dp,Accent,Modifier.size(42.dp).clickable(enabled=!isLoading){onSelect(ns,true)})
    }
    else->Mdi("lock-outline",24.dp,Muted)
}}}}
    }} }
}

private fun detailValueUa(label:String,value:String):String{
    val v=value.trim()
    return when(label){
        "Статус"->when(v.lowercase(Locale.ROOT)){"ended"->"Завершено";"returning series"->"Продовжується";"released"->"Вийшов";"in production"->"У виробництві";"planned"->"Заплановано";else->v}
        "Тип"->when(v.lowercase(Locale.ROOT)){"scripted"->"Сценарний";"documentary"->"Документальний";"reality"->"Реаліті";"news"->"Новини";"talk show"->"Ток-шоу";"miniseries"->"Мінісеріал";else->v}
        "Мова оригіналу"->when(v.lowercase(Locale.ROOT)){"english"->"англійська";"ukrainian"->"українська";"german"->"німецька";"french"->"французька";"spanish"->"іспанська";"italian"->"італійська";"japanese"->"японська";"korean"->"корейська";else->v}
        else->v
    }
}
private fun mediaDurationLabel(seconds:Double):String{val total=seconds.toInt().coerceAtLeast(0);if(total<=0)return "";val mins=(total+30)/60;return if(mins<60)"$mins хв" else {val h=mins/60;val m=mins%60;if(m>0)"$h год $m хв" else "$h год"}}
private fun metadataRuntimeLabel(x:JSONObject):String=x.optString("runtime_label","").trim().ifBlank{val m=x.optInt("runtime_minutes",0);if(m<=0)"" else if(m<60)"$m хв" else {val h=m/60;val r=m%60;if(r>0)"$h год $r хв" else "$h год"}}
private fun historyRuntimeLabel(x:JSONObject):String=x.optString("runtime_label","").trim().ifBlank{x.optInt("runtime_minutes",0).takeIf{it>0}?.let{m->if(m<60)"$m хв" else {val h=m/60;val r=m%60;if(r>0)"$h год $r хв" else "$h год"}}?:""}
private fun detailGenreUa(value:String)=when(value.lowercase(Locale.ROOT)){"crime"->"Кримінал";"drama"->"Драма";"mystery"->"Детектив";"thriller"->"Трилер";"comedy"->"Комедія";"action"->"Бойовик";"adventure"->"Пригоди";"animation"->"Анімація";"documentary"->"Документальний";"family"->"Сімейний";"fantasy"->"Фентезі";"history"->"Історичний";"horror"->"Жахи";"music"->"Музика";"romance"->"Мелодрама";"science fiction"->"Наукова фантастика";"war"->"Військовий";"western"->"Вестерн";else->value}
private fun openCatalogUrl(context:Context,url:String){if(url.isBlank())return;runCatching{context.startActivity(Intent(Intent.ACTION_VIEW,Uri.parse(url)).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))}}
@Composable private fun CatalogMetric(iconRes:Int,label:String,value:String,enabled:Boolean,modifier:Modifier=Modifier,onClick:()->Unit){
    Column(modifier.alpha(if(enabled)1f else .35f).clickable(enabled=enabled,onClick=onClick),horizontalAlignment=Alignment.CenterHorizontally){
        Box(Modifier.height(48.dp).fillMaxWidth(),contentAlignment=Alignment.Center){Image(painterResource(iconRes),label,Modifier.size(35.dp).clip(RoundedCornerShape(6.dp)),contentScale=ContentScale.Fit)}
        Text(value,fontSize=14.sp,fontWeight=FontWeight.Bold,color=Text,textAlign=TextAlign.Center,modifier=Modifier.fillMaxWidth().padding(top=4.dp))
    }
}

@Composable private fun VideoDetailDialog(context:Context,cat:JSONObject,bm:androidx.compose.ui.graphics.ImageBitmap?,external:JSONObject,onDismiss:()->Unit){
    val details=cat.optJSONObject("details")?:JSONObject(); val seasons=cat.optJSONArray("seasons")?.objects().orEmpty().filter{it.optBoolean("released",true)}; val totalEpisodes=seasons.sumOf{it.optInt("episode_count",0)}
    fun arr(name:String)=details.optJSONArray(name)?.strings().orEmpty()
    val tmdbId=cat.optInt("tmdb_id",external.optInt("tmdb_id",0));val imdbId=cat.optString("imdb_id",external.optString("imdb_id")).trim();val mediaType=cat.optString("media_type","movie")
    val tmdbUrl=if(tmdbId>0)"https://www.themoviedb.org/${if(mediaType=="tv")"tv" else "movie"}/$tmdbId" else "";val imdbUrl=if(imdbId.startsWith("tt"))"https://www.imdb.com/title/$imdbId/" else "";val mdblistUrl=if(imdbId.startsWith("tt"))"https://mdblist.com/${if(mediaType=="tv")"show" else "movie"}/$imdbId" else "";val traktUrl=external.optString("trakt_url").trim();val moviebaseUrl=if(tmdbId>0)"https://moviebase.app/${if(mediaType=="tv")"show" else "movie"}/$tmdbId" else external.optString("moviebase_url").trim()
    val tmdbRating=cat.optDouble("tmdb_rating_percent",0.0).takeIf{it>0};val imdbRating=cat.optDouble("imdb_rating",0.0).takeIf{it>0};val mdblistRating=cat.optDouble("mdblist_rating",0.0).takeIf{it>0};val traktRating=cat.optDouble("trakt_rating",0.0).takeIf{it>0};val moviebaseRating=cat.optDouble("moviebase_rating",0.0).takeIf{it>0}
    fun pct(v:Double?)=v?.let{"${"%.0f".format(Locale.US,if(it<=10)it*10 else it)}%"}?:"—"
    fun imdb(v:Double?)=v?.let{"%.1f".format(Locale.US,it)}?:"—"
    @Composable fun Fact(label:String,value:String,modifier:Modifier=Modifier){if(value.isNotBlank())Column(modifier.padding(end=14.dp,bottom=22.dp)){Text(label,fontSize=13.sp,fontWeight=FontWeight.SemiBold,color=Muted);Text(value,fontSize=16.sp,lineHeight=23.sp,fontWeight=FontWeight.Medium,color=Text,modifier=Modifier.padding(top=5.dp))}}
    @Composable fun DetailLine(label:String,value:String){if(value.isNotBlank())Column(Modifier.fillMaxWidth().padding(bottom=22.dp)){Text(label,fontSize=13.sp,fontWeight=FontWeight.SemiBold,color=Muted);Text(value,fontSize=16.sp,lineHeight=23.sp,color=Text,modifier=Modifier.padding(top=5.dp))}}
    Dialog(onDismissRequest=onDismiss,properties=DialogProperties(usePlatformDefaultWidth=false)){Box(Modifier.fillMaxSize().background(Color.Black.copy(alpha=.90f)),contentAlignment=Alignment.BottomCenter){Box(Modifier.matchParentSize().clickable(onClick=onDismiss));Column(Modifier.fillMaxWidth().widthIn(max=620.dp).fillMaxHeight(.96f).clip(RoundedCornerShape(topStart=22.dp,topEnd=22.dp)).background(Color(0xFF100D13)).clickable(enabled=false){}){
        Spacer(Modifier.height(18.dp))
        Column(Modifier.weight(1f).verticalScroll(rememberScrollState()).padding(horizontal=18.dp,vertical=6.dp)){
            if(bm!=null)Box(Modifier.fillMaxWidth(),contentAlignment=Alignment.Center){Image(bm,null,Modifier.width(235.dp).aspectRatio(2f/3f).clip(RoundedCornerShape(18.dp)).clickable(onClick=onDismiss),contentScale=ContentScale.Crop)}
            Text(cat.optString("title","Відео"),fontSize=27.sp,lineHeight=32.sp,fontWeight=FontWeight.Bold,color=Text,textAlign=TextAlign.Center,modifier=Modifier.fillMaxWidth().padding(top=18.dp))
            val status=detailValueUa("Статус",details.optString("status"));val type=detailValueUa("Тип",details.optString("type",contentTypeLabel(cat.optString("content_type"),mediaType)));val topMeta=buildList{cat.optString("year").takeIf{it.isNotBlank()}?.let(::add);status.takeIf{it.isNotBlank()}?.let(::add);type.takeIf{it.isNotBlank()}?.let(::add)}.joinToString(" · ")
            if(topMeta.isNotBlank())Text(topMeta,fontSize=16.sp,lineHeight=22.sp,color=Muted,textAlign=TextAlign.Center,modifier=Modifier.fillMaxWidth().padding(top=8.dp,bottom=12.dp))
            Row(Modifier.fillMaxWidth()){CatalogMetric(R.drawable.tmdb_brand,"TMDB",pct(tmdbRating),tmdbUrl.isNotBlank(),Modifier.weight(1f)){openCatalogUrl(context,tmdbUrl)};CatalogMetric(R.drawable.imdb_brand,"IMDb",imdb(imdbRating),imdbUrl.isNotBlank(),Modifier.weight(1f)){openCatalogUrl(context,imdbUrl)};CatalogMetric(R.drawable.mdblist_brand,"MDbList",pct(mdblistRating),mdblistUrl.isNotBlank(),Modifier.weight(1f)){openCatalogUrl(context,mdblistUrl)};CatalogMetric(R.drawable.trakt_brand,"Trakt",pct(traktRating),traktUrl.isNotBlank(),Modifier.weight(1f)){openCatalogUrl(context,traktUrl)};CatalogMetric(R.drawable.moviebase_brand,"Moviebase",pct(moviebaseRating),moviebaseUrl.isNotBlank(),Modifier.weight(1f)){openCatalogUrl(context,moviebaseUrl)}}
            val overview=cat.optString("overview");if(overview.isNotBlank()){Text(overview,fontSize=16.sp,lineHeight=24.sp,color=Color(0xFFD7DCE1),textAlign=TextAlign.Justify,modifier=Modifier.fillMaxWidth().padding(top=24.dp,bottom=26.dp))}
            Text(ui("Детальна інформація"),fontSize=18.sp,fontWeight=FontWeight.Bold,color=Text,modifier=Modifier.padding(bottom=18.dp))
            val genres=arr("genres").map(::detailGenreUa);if(genres.isNotEmpty())DetailLine("Жанри",genres.joinToString(" · "))
            Row(Modifier.fillMaxWidth()){Fact(ui("Статус"),status,Modifier.weight(1f));Fact(ui("Тип"),type,Modifier.weight(1f))}
            Row(Modifier.fillMaxWidth()){Fact(if(mediaType=="tv")"Час серії" else "Тривалість",metadataRuntimeLabel(details),Modifier.weight(1f));Spacer(Modifier.weight(1f))}
            if(mediaType=="tv")Row(Modifier.fillMaxWidth()){Fact(ui("Сезони"),seasons.size.takeIf{it>0}?.toString().orEmpty(),Modifier.weight(1f));Fact(ui("Кількість серій"),totalEpisodes.takeIf{it>0}?.toString().orEmpty(),Modifier.weight(1f))}
            Row(Modifier.fillMaxWidth()){Fact(ui("Мова оригіналу"),detailValueUa("Мова оригіналу",details.optString("original_language")),Modifier.weight(1f));Fact(ui("Сертифікація"),details.optString("certification"),Modifier.weight(1f))}
            Row(Modifier.fillMaxWidth()){Fact(ui("Дата релізу"),details.optString("release_date"),Modifier.weight(1f));Fact(ui("Оригінальна назва"),cat.optString("original_title"),Modifier.weight(1f))}
            val countries=arr("countries");if(countries.isNotEmpty())DetailLine("Країни",countries.joinToString(" · "));val networks=arr("networks").map{it.removePrefix("See more TV shows from ").removeSuffix("...")};if(networks.isNotEmpty())DetailLine("Мережі",networks.joinToString(" · "));val companies=arr("companies");if(companies.isNotEmpty())DetailLine("Компанії",companies.joinToString(" · "));Spacer(Modifier.height(28.dp))
        }
    }}}
}

@Composable private fun SelectorField(label:String,icon:String,value:String,options:List<String>,showCount:Boolean=false,trailing:String="",iconAction:(()->Unit)?=null,onSelect:(String)->Unit){
    var expanded by remember{mutableStateOf(false)}
    val selectedIndex=options.indexOf(value)
    val labelText=if(showCount&&options.isNotEmpty()) "$label · ${options.size} серій" else label
    val valueText=if(showCount&&selectedIndex>=0&&options.size>1) "$value · ${selectedIndex+1}/${options.size}" else value
    val menuMax=if(showCount&&options.size<=10) 520.dp else 420.dp
    Row(Modifier.fillMaxWidth().heightIn(min=56.dp),verticalAlignment=Alignment.Bottom){
        Box(Modifier.width(28.dp).height(39.dp).padding(bottom=9.dp).then(if(iconAction!=null)Modifier.clip(RoundedCornerShape(8.dp)).clickable{iconAction()} else Modifier),contentAlignment=Alignment.BottomCenter){Mdi(icon,20.dp,if(iconAction!=null)Accent else Color(0xFFD8DDE2))}
        Spacer(Modifier.width(8.dp))
        Column(Modifier.weight(1f)){
            Text(labelText,fontSize=10.sp,color=Color(0xFFC4CAD0),modifier=Modifier.padding(bottom=3.dp))
            Box(Modifier.fillMaxWidth()){
                Row(Modifier.fillMaxWidth().height(39.dp).clip(RoundedCornerShape(9.dp)).background(Input).border(1.dp,Line,RoundedCornerShape(9.dp)).clickable(enabled=options.isNotEmpty()){expanded=true}.padding(start=10.dp,end=9.dp),verticalAlignment=Alignment.CenterVertically){
                    Text(valueText,fontSize=14.sp,color=if(options.isNotEmpty())Text else Text.copy(alpha=.55f),modifier=Modifier.weight(1f),maxLines=1,overflow=TextOverflow.Ellipsis)
                    if(trailing.isNotBlank())Text(trailing,fontSize=11.sp,fontWeight=FontWeight.SemiBold,color=Muted,modifier=Modifier.padding(horizontal=8.dp))
                    Mdi("chevron-down",16.dp,Muted.copy(alpha=if(options.isNotEmpty())1f else .55f))
                }
                DropdownMenu(expanded=expanded,onDismissRequest={expanded=false},modifier=Modifier.heightIn(max=menuMax).background(Color(0xFF171B20)).border(1.dp,Color(0xFF31363C),RoundedCornerShape(12.dp))){
                    options.forEachIndexed{i,v->DropdownMenuItem(text={Row(Modifier.fillMaxWidth(),verticalAlignment=Alignment.CenterVertically){Text(v,color=Text,fontSize=14.sp,modifier=Modifier.weight(1f));if(showCount)Text("${i+1}/${options.size}",color=Muted,fontSize=11.sp)}},onClick={expanded=false;onSelect(v)})}
                }
            }
        }
    }
}

@Composable private fun VideoToggleControl(icon:String,checked:Boolean,enabled:Boolean,onChange:(Boolean)->Unit){
    val active=enabled&&checked
    Row(Modifier.height(48.dp).alpha(if(enabled)1f else .45f).padding(horizontal=2.dp),verticalAlignment=Alignment.CenterVertically,horizontalArrangement=Arrangement.spacedBy(5.dp)){
        Mdi(icon,22.dp,if(active)Accent else Color(0xFFD8DDE2))
        SkeletonSwitch(checked,{if(enabled)onChange(it)},40.dp,24.dp,18.dp)
    }
}

@Composable private fun RoundAction(icon:String,primary:Boolean,onClick:()->Unit){
    Box(Modifier.size(50.dp).clip(CircleShape).background(if(primary)Accent else Color(0xFF171D23)).border(if(primary)0.dp else 1.dp,if(primary)Color.Transparent else Line,CircleShape).clickable(onClick=onClick),contentAlignment=Alignment.Center){
        Mdi(icon,25.dp,if(primary)Color.White else Color(0xFFE7EBEE))
    }
}


@Composable private fun ProviderButton(iconRes:Int,label:String,actionable:Boolean,onClick:()->Unit){
    Box(Modifier.size(52.dp).alpha(if(actionable)1f else .45f).clickable(enabled=actionable,onClick=onClick),contentAlignment=Alignment.Center){
        Image(painterResource(iconRes),label,Modifier.fillMaxSize(),contentScale=ContentScale.Fit)
    }
}

private fun openProviderApp(context:Context,trakt:Boolean,pkg:String?,ext:JSONObject,cat:JSONObject){
    if(pkg==null)return
    val id=ext.optInt("tmdb_id",0)
    val type=cat.optString("media_type",if(cat.optString("content_type").contains("series",true))"tv" else "movie")
    if(trakt){
        val raw=ext.optString("trakt_url").trim()
        if(raw.isBlank()){
            Toast.makeText(context,"Для цього твору ще немає точної сторінки Trakt",Toast.LENGTH_LONG).show();return
        }
        val candidates=listOf(raw,raw.replace("https://app.trakt.tv/","https://trakt.tv/" )).distinct()
        for(u in candidates){
            val ok=runCatching{
                val i=Intent(Intent.ACTION_VIEW,Uri.parse(u)).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP)
                if(pkg=="tv.trakt.trakt")i.setClassName("tv.trakt.trakt","tv.trakt.trakt.MainActivity") else i.setPackage(pkg)
                context.startActivity(i);true
            }.getOrDefault(false)
            if(ok)return
        }
        Toast.makeText(context,"Trakt не зміг відкрити сторінку цього твору",Toast.LENGTH_LONG).show();return
    }
    if(id<=0){Toast.makeText(context,"Для цього твору ще немає TMDB ID",Toast.LENGTH_LONG).show();return}
    val uri="https://www.themoviedb.org/${if(type=="tv")"tv" else "movie"}/$id"
    val ok=runCatching{
        context.startActivity(Intent(Intent.ACTION_VIEW,Uri.parse(uri)).setPackage(pkg).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP));true
    }.getOrDefault(false)
    if(!ok)Toast.makeText(context,"Moviebase не зміг відкрити сторінку цього твору",Toast.LENGTH_LONG).show()
}

private data class NativeDeviceUi(val id:String,val name:String,val role:String,val adapter:String,val online:Boolean,val location:String,val dependencies:List<String>,val controller:String,val domain:String,val connection:String)
private val deviceDomainOrder=listOf("Житло","Мережа","Керування","Документи","Київ / поза домом")
private fun nativeDeviceDomain(id:String,role:String,location:String):String{
    if(id.startsWith("kyiv_")||location.startsWith("kyiv_"))return "Київ / поза домом"
    return when(role){
        "gateway_router_modem","secondary_router","media_pc","virtualization_host"->"Мережа"
        "air_mouse_remote","android_phone","ios_phone","maintenance_keyboard","maintenance_mouse","physical_gamepad","tablet_kiosk"->"Керування"
        "printer_scanner"->"Документи"
        else->"Житло"
    }
}
private fun nativeLocationLabel(location:String)=when(location){
    "living_room"->"Вітальня"
    "living_room_tv"->"Вітальня · телевізор"
    "living_room_right"->"Вітальня · праворуч"
    "living_room_left_cabinet"->"Вітальня · ліва тумба"
    "kyiv_apartment"->"Київ · квартира"
    "kyiv_apartment_tv"->"Київ · телевізор"
    else->""
}
private fun nativeDeviceConnection(id:String,role:String,adapter:String,location:String,dependencies:List<String>,controller:String):String{
    val where=nativeLocationLabel(location)
    val relation=when{
        controller.isNotBlank()->"Керується через $controller"
        dependencies.isNotEmpty()&&role in setOf("air_mouse_remote","maintenance_keyboard","maintenance_mouse","physical_gamepad")->"Підключено до ${dependencies.joinToString(" та ")}"
        dependencies.isNotEmpty()&&role=="television_display"->"Працює разом із ${dependencies.joinToString(" та ")}"
        dependencies.isNotEmpty()&&role=="infrared_tv_controller"->"Пов’язано з ${dependencies.joinToString(" та ")}"
        dependencies.isNotEmpty()->"Пов’язано з ${dependencies.joinToString(" та ")}"
        role=="gateway_router_modem"->"Головний вихід домашньої мережі в Інтернет"
        role=="secondary_router"->"Додаткова точка домашньої мережі"
        role=="media_pc"->"Центральний домашній медіавузол"
        role=="tv_backlight"->"Підсвітка телевізора через домашню мережу"
        role=="decorative_planter_light"||role=="lava_lamp"->"Освітлення через домашню мережу"
        role=="smart_speaker"->"Колонка в домашній мережі"
        role=="soundbar"->"Аудіосистема вітальні"
        role=="printer_scanner"->"Принтер і сканер у домашній мережі"
        role=="android_phone"||role=="ios_phone"->"Телефон у домашній мережі"
        role=="tablet_kiosk"->"Домашня панель у мережі"
        adapter=="wled"->"Освітлення в домашній мережі"
        adapter=="network"->"Домашня мережа"
        adapter=="host"->"Працює разом із Home Edge"
        else->"Зв’язок ще не описаний"
    }
    return listOf(where,relation).filter{it.isNotBlank()}.joinToString(" · ")
}
private fun nativeDeviceIcon(role:String)=when(role){
    "television_display"->"television"
    "physical_gamepad"->"gamepad-variant"
    "air_mouse_remote","maintenance_keyboard","maintenance_mouse","android_phone","ios_phone","tablet_kiosk"->"devices"
    "tv_backlight","decorative_planter_light","lava_lamp"->"lightbulb-outline"
    "gateway_router_modem","secondary_router","media_pc","virtualization_host"->"cast-connected"
    else->"devices"
}
@Composable private fun DeviceDomainCard(title:String,items:List<NativeDeviceUi>,onSharp:()->Unit,onMfp:()->Unit,onHomeEdge:()->Unit){
    Column(Modifier.fillMaxWidth().clip(RoundedCornerShape(16.dp)).background(Card)){
        Row(Modifier.fillMaxWidth().padding(horizontal=15.dp,vertical=12.dp),verticalAlignment=Alignment.CenterVertically){Text(title,fontSize=15.sp,fontWeight=FontWeight.Bold,color=Text,modifier=Modifier.weight(1f));Text(ui("${items.count{it.online}}/${items.size} на зв’язку"),fontSize=10.sp,color=Muted)}
        items.forEachIndexed{index,d->
            if(index>0)Box(Modifier.fillMaxWidth().height(1.dp).background(Line))
            Row(Modifier.fillMaxWidth().heightIn(min=76.dp).clickable(enabled=d.id=="sharp_tv_living_room"||d.id=="brother_printer"||d.id=="home_edge_01"){when(d.id){"sharp_tv_living_room"->onSharp();"brother_printer"->onMfp();"home_edge_01"->onHomeEdge()}}.padding(horizontal=14.dp,vertical=10.dp),verticalAlignment=Alignment.CenterVertically){
                Box(Modifier.size(42.dp).clip(RoundedCornerShape(12.dp)).background(Action),contentAlignment=Alignment.Center){Mdi(nativeDeviceIcon(d.role),23.dp,if(d.online)Text else Muted)}
                Column(Modifier.weight(1f).padding(start=12.dp)){Text(d.name,fontSize=17.sp,lineHeight=22.sp,fontWeight=FontWeight.SemiBold,color=Text,maxLines=2,overflow=TextOverflow.Ellipsis);Text(d.connection,fontSize=10.sp,lineHeight=14.sp,color=Muted,maxLines=3,overflow=TextOverflow.Ellipsis,modifier=Modifier.padding(top=4.dp))}
                Column(horizontalAlignment=Alignment.End){Box(Modifier.size(8.dp).clip(CircleShape).background(if(d.online)Green else Muted));Text(if(d.online)"На зв’язку" else "Не на зв’язку",fontSize=9.sp,color=if(d.online)Green else Muted,modifier=Modifier.padding(top=4.dp));if(d.id=="sharp_tv_living_room"||d.id=="brother_printer"||d.id=="home_edge_01")Mdi("chevron-right",18.dp,Muted,Modifier.padding(top=5.dp))}
            }
        }
    }
}

private class SharpIrBridge(private val context:Context){
    private val ir=context.getSystemService(Context.CONSUMER_IR_SERVICE) as? ConsumerIrManager
    @Volatile private var currentPageUrl:String=""
    @Volatile private var lastError:String=""
    fun updatePage(url:String?){currentPageUrl=url.orEmpty()}
    @JavascriptInterface fun hasIr():Boolean=ir?.hasIrEmitter()==true
    @JavascriptInterface fun appVersion():String=runCatching{context.packageManager.getPackageInfo(context.packageName,0).versionName.orEmpty()}.getOrDefault("")
    @JavascriptInterface fun lastIrError():String=lastError
    @JavascriptInterface fun irTransmit(carrierFrequency:Int,patternJson:String,candidateId:String):Boolean{
        fun fail(message:String):Boolean{lastError=message;return false}
        return try{
            if(!currentPageUrl.contains("/ir/sharp"))return fail("Сторінка ІЧ не активна.")
            if(ir?.hasIrEmitter()!=true)return fail("На телефоні не знайдено ІЧ-випромінювач.")
            if(carrierFrequency !in 30000..60000)return fail("Недопустима частота.")
            val values=JSONArray(patternJson);if(values.length()<2||values.length()>512)return fail("Недопустима довжина коду.")
            val pattern=IntArray(values.length());var total=0L;val canonical=StringBuilder().append(carrierFrequency).append(':')
            for(i in 0 until values.length()){val d=values.getInt(i);if(d !in 1..100000)return fail("Недопустимий імпульс.");total+=d;if(total>=1900000L)return fail("ІЧ-код надто довгий.");if(i>0)canonical.append(',');canonical.append(d);pattern[i]=d}
            val digest=MessageDigest.getInstance("SHA-256").digest(canonical.toString().toByteArray()).joinToString(""){"%02x".format(it)}
            if(candidateId.isBlank()||!(candidateId.endsWith("-${digest.take(12)}")||candidateId=="sharp-${digest.take(16)}"))return fail("Код не збігається з тестовим банком.")
            ir.transmit(carrierFrequency,pattern);lastError="";true
        }catch(e:Exception){fail(e.javaClass.simpleName+": "+(e.message?:"помилка передачі"))}
    }
}


private fun mfpRecipientLabel(raw:String):String=when{
    raw.contains("Liudmyla",true)->"Людмила"
    raw.contains("Artemii",true)->"Артемій"
    raw.contains("Oleksii",true)->"Олексій"
    raw.isBlank()->"Не визначено"
    else->raw
}
private fun mfpStageLabel(stage:String)=when(stage){
    "scanning"->"Сканує"
    "collecting"->"Збирає сторінки"
    "assembling"->"Зшиває PDF"
    "recognizing"->"Розпізнає"
    "reporting"->"Формує звіт"
    "processing"->"Обробляє"
    "done"->"Готово"
    "failed"->"Помилка"
    "cancelled"->"Зупинено"
    else->"Очікує"
}
private fun mfpStatusColor(stage:String)=when(stage){
    "done"->Green
    "failed"->Color(0xFFFF8A80)
    "scanning","collecting","assembling","recognizing","reporting","processing"->Accent
    else->Muted
}
private fun mfpTime(seconds:Int):String{val s=seconds.coerceAtLeast(0);return if(s<60)"${s} с" else "${s/60} хв ${s%60} с"}
private fun mfpScanDateLabel(raw:String):String{
    if(raw.isBlank())return ""
    return runCatching{
        val z=java.time.OffsetDateTime.parse(raw).toInstant().atZone(java.time.ZoneId.systemDefault())
        java.time.format.DateTimeFormatter.ofPattern("dd.MM.yyyy · HH:mm").format(z)
    }.getOrElse{raw.replace('T',' ').take(16)}
}
private fun mfpStatusHuman(raw:String)=when(raw.uppercase(Locale.ROOT)){"DONE"->"Розпізнано";"REVIEW"->"Потрібно перевірити";"FAILED"->"Помилка";else->raw.replace('_',' ').lowercase().replaceFirstChar{it.uppercase()}}
private fun mfpReviewReasonHuman(raw:String)=when(raw){"translation_quality_review"->"Потрібно перевірити якість розпізнавання";"ambiguous_single_pass_boundaries"->"Не вдалося надійно визначити межі документа";"unresolved_single_page"->"Сторінка потребує ручної перевірки";""->"";else->"Потрібно перевірити результат розпізнавання"}
private fun downloadMfpPdf(context:Context,api:HomeApi,doc:JSONObject){
    val base=api.server?:return
    val id=doc.optString("id");if(id.isBlank())return
    runCatching{
        val dm=context.getSystemService(Context.DOWNLOAD_SERVICE) as DownloadManager
        val raw=(doc.optString("title","scan")+".pdf")
        val title=raw.replace(Regex("[\\/:*?\"<>|]"),"_")
        val req=DownloadManager.Request(Uri.parse("$base/api/native/mfp/document/${Uri.encode(id)}/pdf"))
            .setTitle(title).setMimeType("application/pdf")
            .setNotificationVisibility(DownloadManager.Request.VISIBILITY_VISIBLE_NOTIFY_COMPLETED)
            .setDestinationInExternalPublicDir(android.os.Environment.DIRECTORY_DOWNLOADS,title)
        dm.enqueue(req)
        Toast.makeText(context,"PDF завантажується",Toast.LENGTH_SHORT).show()
    }.onFailure{Toast.makeText(context,it.message?:"Не вдалося завантажити PDF",Toast.LENGTH_LONG).show()}
}

private fun shareMfpPdf(context:Context,api:HomeApi,doc:JSONObject){
    val base=api.server?:return
    val id=doc.optString("id");if(id.isBlank())return
    val raw=(doc.optString("title","scan")+".pdf")
    val safe=raw.replace(Regex("[\\/:*?\"<>|]"),"_")
    kotlinx.coroutines.CoroutineScope(Dispatchers.IO).launch{
        runCatching{
            val dir=File(context.cacheDir,"shared-pdfs").apply{mkdirs()}
            val out=File(dir,safe)
            val c=URL("$base/api/native/mfp/document/${Uri.encode(id)}/pdf").openConnection() as HttpURLConnection
            c.connectTimeout=8000;c.readTimeout=60000;c.requestMethod="GET"
            if(c.responseCode !in 200..299)throw IllegalStateException("PDF HTTP ${c.responseCode}")
            c.inputStream.use{input->out.outputStream().use{output->input.copyTo(output)}}
            if(out.length()<=0)throw IllegalStateException("Порожній PDF")
            val uri=FileProvider.getUriForFile(context,"${context.packageName}.files",out)
            val intent=Intent(Intent.ACTION_SEND).apply{type="application/pdf";putExtra(Intent.EXTRA_STREAM,uri);addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION or Intent.FLAG_ACTIVITY_NEW_TASK)}
            withContext(Dispatchers.Main){context.startActivity(Intent.createChooser(intent,"Відправити документ").addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))}
        }.onFailure{e->withContext(Dispatchers.Main){Toast.makeText(context,e.message?:"Не вдалося підготувати PDF",Toast.LENGTH_LONG).show()}}
    }
}

@Composable private fun MfpFact(label:String,value:String){
    val clean=value.trim();if(clean.isBlank()||clean.equals("null",true)||clean.equals("none",true))return
    Column(Modifier.fillMaxWidth().padding(vertical=7.dp)){
        Text(label,fontSize=13.sp,fontWeight=FontWeight.SemiBold,color=Muted)
        Text(clean,fontSize=16.sp,lineHeight=22.sp,color=Text,modifier=Modifier.padding(top=3.dp))
    }
}

@Composable private fun MfpDocumentDetailDialog(api:HomeApi,item:JSONObject,onDismiss:()->Unit){
    val context=LocalContext.current
    var detail by remember(item){mutableStateOf(JSONObject())}
    var loading by remember(item){mutableStateOf(true)}
    var readingMode by rememberSaveable(item.optString("id")){mutableStateOf("summary")}
    var fullTextUk by remember(item){mutableStateOf("")}
    var fullTextLoaded by remember(item){mutableStateOf(false)}
    var fullTextLoading by remember(item){mutableStateOf(false)}
    LaunchedEffect(item.optString("id")){
        loading=true
        detail=runCatching{api.get("/api/native/mfp/document/${Uri.encode(item.optString("id"))}")}.getOrElse{JSONObject()}
        loading=false
    }
    LaunchedEffect(readingMode,item.optString("id")){
        if(readingMode=="full"&&!fullTextLoaded){
            fullTextLoading=true
            val res=runCatching{api.get("/api/native/mfp/document/${Uri.encode(item.optString("id"))}/translation-uk")}.getOrNull()
            fullTextUk=res?.optString("text").orEmpty()
            fullTextLoaded=true;fullTextLoading=false
        }
    }
    Dialog(onDismissRequest=onDismiss,properties=DialogProperties(usePlatformDefaultWidth=false)){
        Box(Modifier.fillMaxSize().background(Color.Black.copy(alpha=.78f)),contentAlignment=Alignment.BottomCenter){
            Box(Modifier.matchParentSize().clickable(onClick=onDismiss))
            Column(Modifier.fillMaxWidth().widthIn(max=620.dp).fillMaxHeight(.94f).clip(RoundedCornerShape(topStart=20.dp,topEnd=20.dp)).background(Input).border(1.dp,Line,RoundedCornerShape(topStart=20.dp,topEnd=20.dp)).clickable(enabled=false){}){
                Row(Modifier.fillMaxWidth().height(58.dp).padding(horizontal=15.dp),verticalAlignment=Alignment.CenterVertically){
                    Text(ui("Документ"),fontSize=20.sp,fontWeight=FontWeight.Bold,color=Text,modifier=Modifier.weight(1f))
                }
                if(loading){
                    Box(Modifier.fillMaxSize(),contentAlignment=Alignment.Center){CircularProgressIndicator(color=Accent)}
                }else{
                    val sum=detail.optJSONObject("summary")?:item
                    val points=sum.optJSONArray("important_points")?.strings().orEmpty()
                    val summary=sum.optString("summary")
                    LazyColumn(Modifier.weight(1f).padding(horizontal=15.dp),contentPadding=PaddingValues(bottom=20.dp),verticalArrangement=Arrangement.spacedBy(12.dp)){
                        item{
                            Text(sum.optString("document_type",sum.optString("title","Документ")),fontSize=22.sp,lineHeight=28.sp,fontWeight=FontWeight.Bold,color=Text)
                            val meta=listOf(sum.optString("document_date"),mfpRecipientLabel(sum.optString("recipient")),sum.optString("issuer")).filter{it.isNotBlank()}.joinToString(" · ")
                            if(meta.isNotBlank())Text(meta,fontSize=14.sp,lineHeight=20.sp,color=Accent,modifier=Modifier.padding(top=6.dp))
                        }
                        item{
                            Row(Modifier.fillMaxWidth().clip(RoundedCornerShape(13.dp)).background(Card).padding(4.dp),horizontalArrangement=Arrangement.spacedBy(4.dp)){
                                listOf("summary" to "Резюме","full" to "Повний текст").forEach{(mode,label)->
                                    val on=readingMode==mode
                                    Box(Modifier.weight(1f).height(44.dp).clip(RoundedCornerShape(10.dp)).background(if(on)Accent else Color.Transparent).clickable{readingMode=mode},contentAlignment=Alignment.Center){Text(label,fontSize=14.sp,fontWeight=FontWeight.Bold,color=if(on)Color.White else Text)}
                                }
                            }
                        }
                        if(readingMode=="summary"){
                            item{
                                Column(Modifier.fillMaxWidth().clip(RoundedCornerShape(16.dp)).background(Card).border(1.dp,Line,RoundedCornerShape(16.dp)).padding(16.dp)){
                                    Text(ui("Резюме українською"),fontSize=18.sp,fontWeight=FontWeight.Bold,color=Text)
                                    Text(if(summary.isNotBlank())summary else "Резюме ще не готове.",fontSize=16.sp,lineHeight=24.sp,color=Color(0xFFD7DCE1),modifier=Modifier.padding(top=10.dp))
                                }
                            }
                            if(points.isNotEmpty())item{
                                Column(Modifier.fillMaxWidth().clip(RoundedCornerShape(16.dp)).background(Card).border(1.dp,Line,RoundedCornerShape(16.dp)).padding(16.dp)){
                                    Text(ui("Важливе"),fontSize=18.sp,fontWeight=FontWeight.Bold,color=Text)
                                    Text(points.joinToString("\n\n"){"• $it"},fontSize=16.sp,lineHeight=24.sp,color=Color(0xFFD7DCE1),modifier=Modifier.padding(top=10.dp))
                                }
                            }
                        }else item{
                            Column(Modifier.fillMaxWidth().clip(RoundedCornerShape(16.dp)).background(Card).border(1.dp,Line,RoundedCornerShape(16.dp)).padding(16.dp)){
                                Text(ui("Повний текст українською"),fontSize=18.sp,fontWeight=FontWeight.Bold,color=Text)
                                when{
                                    fullTextLoading->Row(Modifier.padding(top=16.dp),verticalAlignment=Alignment.CenterVertically){CircularProgressIndicator(Modifier.size(22.dp),strokeWidth=2.dp,color=Accent);Spacer(Modifier.width(10.dp));Text(ui("Завантажую переклад…"),fontSize=16.sp,color=Muted)}
                                    fullTextUk.isNotBlank()->Text(fullTextUk,fontSize=16.sp,lineHeight=25.sp,color=Color(0xFFD7DCE1),modifier=Modifier.padding(top=12.dp))
                                    else->Text(ui("Український переклад цього документа ще не готовий. Після повторної обробки він з’явиться тут автоматично."),fontSize=16.sp,lineHeight=24.sp,color=Muted,modifier=Modifier.padding(top=12.dp))
                                }
                            }
                        }
                        item{
                            Column(Modifier.fillMaxWidth().clip(RoundedCornerShape(14.dp)).background(Card).padding(16.dp)){
                                Text(ui("Основні дані"),fontSize=18.sp,fontWeight=FontWeight.Bold,color=Text)
                                MfpFact(ui("Для кого"),mfpRecipientLabel(sum.optString("recipient")))
                                MfpFact(ui("Відправник"),sum.optString("issuer"))
                                MfpFact(ui("Тип документа"),sum.optString("document_type"))
                                MfpFact(ui("Дата документа"),sum.optString("document_date"))
                                MfpFact(ui("Дата сканування"),mfpScanDateLabel(sum.optString("scan_date")))
                                MfpFact(ui("Тема"),sum.optString("topic").replace(Regex("^\\d{2}\\s+"),""))
                                MfpFact(ui("Країна"),sum.optString("country"))
                                MfpFact(ui("Сторінок"),sum.optInt("page_count",0).takeIf{it>0}?.toString().orEmpty())
                                MfpFact(ui("Потрібна дія"),if(sum.optBoolean("action_required"))"Так" else "Ні")
                                MfpFact(ui("Строк"),sum.optString("deadline"))
                                MfpFact(ui("Потрібна перевірка"),if(sum.optBoolean("review_required"))"Так" else "Ні")
                                if(sum.optBoolean("review_required"))MfpFact(ui("Що треба перевірити"),mfpReviewReasonHuman(sum.optString("review_reason")))
                                val conf=sum.optDouble("classification_confidence",0.0)
                                MfpFact(ui("Впевненість розпізнавання"),if(conf>0)"${(conf*100).toInt()}%" else "")
                            }
                        }
                    }
                    Row(Modifier.fillMaxWidth().padding(12.dp),horizontalArrangement=Arrangement.spacedBy(10.dp)){
                        Button(onClick={downloadMfpPdf(context,api,item)},enabled=item.optBoolean("pdf_available"),shape=RoundedCornerShape(12.dp),colors=ButtonDefaults.buttonColors(containerColor=Accent),modifier=Modifier.weight(1f).height(50.dp)){
                            Mdi("download",20.dp,Color.White);Spacer(Modifier.width(7.dp));Text(ui("Завантажити"),fontSize=14.sp,fontWeight=FontWeight.Bold)
                        }
                        OutlinedButton(onClick={shareMfpPdf(context,api,item)},enabled=item.optBoolean("pdf_available"),shape=RoundedCornerShape(12.dp),modifier=Modifier.weight(1f).height(50.dp)){
                            Mdi("share-variant",20.dp,Text);Spacer(Modifier.width(7.dp));Text(ui("Відправити"),fontSize=14.sp,fontWeight=FontWeight.Bold,color=Text)
                        }
                    }
                }
            }
        }
    }
}

@Composable private fun MfpDocumentRow(item:JSONObject,onClick:()->Unit){
    Column(Modifier.fillMaxWidth().clip(RoundedCornerShape(13.dp)).background(Card).clickable(onClick=onClick).padding(12.dp)){
        Row(verticalAlignment=Alignment.Top){
            Column(Modifier.weight(1f)){
                Text(item.optString("document_type",item.optString("title","Документ")),fontSize=14.sp,fontWeight=FontWeight.SemiBold,color=Text,maxLines=2,overflow=TextOverflow.Ellipsis)
                val meta=listOf(mfpRecipientLabel(item.optString("recipient")),item.optString("issuer"),item.optString("document_date")).filter{it.isNotBlank()}.joinToString(" · ")
                if(meta.isNotBlank())Text(meta,fontSize=13.sp,lineHeight=18.sp,color=Accent,modifier=Modifier.padding(top=4.dp))
                val scanned=mfpScanDateLabel(item.optString("scan_date"));if(scanned.isNotBlank())Text(ui("Скановано: $scanned"),fontSize=12.sp,color=Muted,modifier=Modifier.padding(top=3.dp))
                val summary=item.optString("summary")
                if(summary.isNotBlank())Text(summary,fontSize=14.sp,lineHeight=20.sp,color=Muted,maxLines=4,overflow=TextOverflow.Ellipsis,modifier=Modifier.padding(top=7.dp))
            }
            Mdi("chevron-right",20.dp,Muted)
        }
        Row(Modifier.fillMaxWidth().padding(top=7.dp),verticalAlignment=Alignment.CenterVertically){
            item.optInt("page_count").takeIf{it>0}?.let{Text(ui("$it стор."),fontSize=12.sp,color=Muted)}
            if(item.optBoolean("action_required")){Spacer(Modifier.width(8.dp));Text(ui("Потрібна дія"),fontSize=12.sp,color=Warn)}
            if(item.optBoolean("review_required")){Spacer(Modifier.width(8.dp));Text(ui("Перевірити"),fontSize=12.sp,color=Color(0xFFFF8A80))}
        }
    }
}

@Composable private fun MfpDialog(api:HomeApi,onDismiss:()->Unit){
    val context=LocalContext.current
    val scope=rememberCoroutineScope()
    var status by remember{mutableStateOf(JSONObject())}
    var docs by remember{mutableStateOf(emptyList<JSONObject>())}
    var query by remember{mutableStateOf("")}
    var recipient by remember{mutableStateOf("Усі")}
    var sortMode by remember{mutableStateOf("Нові")}
    var selected by remember{mutableStateOf<JSONObject?>(null)}
    var busy by remember{mutableStateOf(false)}
    val focusRequester=remember{FocusRequester()};val keyboard=LocalSoftwareKeyboardController.current
    var cameraUri by remember{mutableStateOf<Uri?>(null)}
    var cameraUploading by remember{mutableStateOf(false)}
    val cameraLauncher=rememberLauncherForActivityResult(ActivityResultContracts.TakePicture()){ok->
        val uri=cameraUri
        if(ok&&uri!=null){scope.launch{cameraUploading=true;runCatching{api.uploadCamera(context,uri)}.onSuccess{Toast.makeText(context,"Фото передано в конвеєр",Toast.LENGTH_SHORT).show()}.onFailure{Toast.makeText(context,it.message?:"Не вдалося передати фото",Toast.LENGTH_LONG).show()};cameraUploading=false}}
    }
    suspend fun refresh(){
        runCatching{api.get("/api/native/mfp/status")}.onSuccess{status=it}
        runCatching{api.get("/api/native/mfp/documents")}.onSuccess{docs=it.optJSONArray("items")?.objects().orEmpty()}
    }
    LaunchedEffect(api.server){while(true){refresh();delay(if(status.optBoolean("active"))1500 else 5000)}}
    val q=query.trim().lowercase(Locale.ROOT)
    var visible=docs.filter{d->
        (recipient=="Усі"||mfpRecipientLabel(d.optString("recipient"))==recipient) &&
        (q.isBlank()||listOf(d.optString("title"),d.optString("recipient"),d.optString("issuer"),d.optString("document_type"),d.optString("summary"),d.optString("topic"),d.optString("category"),d.optString("scan_date")).joinToString(" ").lowercase(Locale.ROOT).contains(q))
    }
    visible=when(sortMode){
        "Для кого"->visible.sortedWith(compareBy({mfpRecipientLabel(it.optString("recipient"))},{it.optString("document_date")}))
        "Відправник"->visible.sortedBy{it.optString("issuer")}
        "Старі"->visible.sortedBy{it.optString("scan_date")}
        else->visible.sortedByDescending{it.optString("scan_date")}
    }
    Dialog(onDismissRequest=onDismiss,properties=DialogProperties(usePlatformDefaultWidth=false)){
        Box(Modifier.fillMaxSize().background(Color.Black.copy(alpha=.78f)),contentAlignment=Alignment.BottomCenter){
            Box(Modifier.matchParentSize().clickable(onClick=onDismiss))
            Column(Modifier.fillMaxWidth().widthIn(max=620.dp).fillMaxHeight(.96f).clip(RoundedCornerShape(topStart=22.dp,topEnd=22.dp)).background(Input).border(1.dp,Line,RoundedCornerShape(topStart=22.dp,topEnd=22.dp)).clickable(enabled=false){}){
                Spacer(Modifier.height(10.dp))
                Row(Modifier.fillMaxWidth().padding(horizontal=12.dp,vertical=4.dp),verticalAlignment=Alignment.CenterVertically){
                    Text("МФУ",fontSize=19.sp,fontWeight=FontWeight.Bold,color=Text,modifier=Modifier.weight(1f))
                    Box(Modifier.clip(RoundedCornerShape(11.dp)).background(Card).border(1.dp,Line,RoundedCornerShape(11.dp)).padding(horizontal=13.dp,vertical=8.dp)){
                        Row(verticalAlignment=Alignment.CenterVertically){Mdi("file-document-multiple-outline",18.dp,Accent);Spacer(Modifier.width(6.dp));Text("Скани · ${docs.size}",fontSize=12.sp,fontWeight=FontWeight.Bold,color=Text)}
                    }
                }
                LazyColumn(Modifier.weight(1f).padding(horizontal=12.dp),contentPadding=PaddingValues(bottom=18.dp),verticalArrangement=Arrangement.spacedBy(10.dp)){
                    item{
                        Column(Modifier.fillMaxWidth().clip(RoundedCornerShape(15.dp)).background(Card).border(1.dp,Line,RoundedCornerShape(15.dp)).padding(14.dp)){
                            Row(verticalAlignment=Alignment.CenterVertically){
                                Box(Modifier.size(9.dp).clip(CircleShape).background(if(status.optBoolean("online"))Green else Color(0xFFFF8A80)))
                                Spacer(Modifier.width(8.dp));Text(if(status.optBoolean("online"))"МФУ на зв’язку" else "МФУ недоступний",fontSize=12.sp,fontWeight=FontWeight.Bold,color=Text)
                                Spacer(Modifier.weight(1f));if(status.optInt("elapsed_seconds")>0)Text(mfpTime(status.optInt("elapsed_seconds")),fontSize=10.sp,color=Muted)
                            }
                            Text(mfpStageLabel(status.optString("stage")),fontSize=21.sp,fontWeight=FontWeight.Bold,color=mfpStatusColor(status.optString("stage")),modifier=Modifier.padding(top=10.dp))
                            Text(status.optString("message","Готово"),fontSize=11.sp,color=Muted,modifier=Modifier.padding(top=3.dp))
                            val pages=status.optInt("pages");if(pages>0)Text(ui("Сторінок: $pages"),fontSize=14.sp,color=Text,modifier=Modifier.padding(top=6.dp))
                            Button(onClick={scope.launch{busy=true;runCatching{api.post(if(status.optBoolean("active"))"/api/native/mfp/stop" else "/api/native/mfp/scan",JSONObject())};delay(500);refresh();busy=false}},enabled=!busy&&status.optBoolean("online"),shape=RoundedCornerShape(12.dp),colors=ButtonDefaults.buttonColors(containerColor=if(status.optBoolean("active"))Color(0xFFA92F35) else Accent),modifier=Modifier.fillMaxWidth().padding(top=10.dp).height(48.dp)){
                                Mdi(if(status.optBoolean("active"))"stop" else "scanner",20.dp,Color.White);Spacer(Modifier.width(8.dp));Text(if(status.optBoolean("active"))"Стоп" else "Сканувати",fontSize=16.sp,fontWeight=FontWeight.Bold)
                            }
                            OutlinedButton(onClick={
                                val dir=File(context.cacheDir,"camera-scan").apply{mkdirs()};val file=File(dir,"scan-${System.currentTimeMillis()}.jpg");val uri=FileProvider.getUriForFile(context,"${context.packageName}.files",file);cameraUri=uri;cameraLauncher.launch(uri)
                            },enabled=!status.optBoolean("active")&&!cameraUploading,shape=RoundedCornerShape(12.dp),modifier=Modifier.fillMaxWidth().padding(top=8.dp).height(48.dp)){
                                Mdi("camera",20.dp,Text);Spacer(Modifier.width(8.dp));Text(if(cameraUploading)"Передаю фото…" else "Сканувати камерою",fontSize=15.sp,fontWeight=FontWeight.Bold,color=Text)
                            }
                        }
                    }
                    item{
                        Text(ui("Журнал сканування"),fontSize=19.sp,fontWeight=FontWeight.Bold,color=Text)
                        Row(Modifier.fillMaxWidth().height(42.dp).clip(RoundedCornerShape(11.dp)).background(Bg).border(1.dp,Line,RoundedCornerShape(11.dp)).clickable{focusRequester.requestFocus();keyboard?.show()}.padding(horizontal=10.dp),verticalAlignment=Alignment.CenterVertically){
                            Mdi("magnify",19.dp,Muted);Spacer(Modifier.width(7.dp))
                            androidx.compose.foundation.text.BasicTextField(value=query,onValueChange={query=it},singleLine=true,textStyle=androidx.compose.ui.text.TextStyle(color=Text,fontSize=15.sp),modifier=Modifier.weight(1f).focusRequester(focusRequester),decorationBox={inner->Box(Modifier.fillMaxWidth()){if(query.isBlank())Text(ui("Пошук по документах"),fontSize=15.sp,color=Muted);inner()}})
                        }
                        Row(Modifier.fillMaxWidth().padding(top=8.dp),horizontalArrangement=Arrangement.spacedBy(6.dp)){listOf("Усі","Олексій","Людмила","Артемій").forEach{r->FilterChip(selected=recipient==r,onClick={recipient=r},label={Text(r,fontSize=10.sp)})}}
                        Row(Modifier.fillMaxWidth(),horizontalArrangement=Arrangement.spacedBy(6.dp)){listOf("Нові","Старі","Для кого","Відправник").forEach{r->FilterChip(selected=sortMode==r,onClick={sortMode=r},label={Text(r,fontSize=10.sp)})}}
                        Text(ui("${visible.size} документів"),fontSize=10.sp,color=Muted)
                    }
                    items(visible,key={it.optString("id")}){d->MfpDocumentRow(d){selected=d}}
                }
            }
            selected?.let{doc->MfpDocumentDetailDialog(api,doc){selected=null}}
        }
    }
}

@Composable private fun SharpRemoteDialog(url:String,onDismiss:()->Unit){
    Dialog(onDismissRequest=onDismiss,properties=DialogProperties(usePlatformDefaultWidth=false)){
        Box(Modifier.fillMaxSize().background(Color.Black.copy(alpha=.82f)),contentAlignment=Alignment.BottomCenter){
            Box(Modifier.matchParentSize().clickable(onClick=onDismiss))
            Column(Modifier.fillMaxWidth().widthIn(max=620.dp).fillMaxHeight(.96f).clip(RoundedCornerShape(topStart=22.dp,topEnd=22.dp)).background(Color(0xFF100D13)).clickable(enabled=false){}){
                Spacer(Modifier.height(18.dp))
                AndroidView(factory={ctx->val bridge=SharpIrBridge(ctx);WebView(ctx).apply{setBackgroundColor(android.graphics.Color.TRANSPARENT);addJavascriptInterface(bridge,"HomeApp");webViewClient=object:WebViewClient(){override fun onPageFinished(view:WebView?,pageUrl:String?){bridge.updatePage(pageUrl)}};settings.javaScriptEnabled=true;settings.domStorageEnabled=true;settings.loadWithOverviewMode=true;settings.useWideViewPort=true;loadUrl(url)}},update={wv->if(wv.url!=url)wv.loadUrl(url)},modifier=Modifier.fillMaxSize())
            }
        }
    }
}


@Composable private fun FamilyScannerScreen(api:HomeApi,active:Boolean,hyperion:Boolean,onHyperion:(Boolean)->Unit,outputTarget:String,onOutputTarget:(String)->Unit,onMenu:()->Unit){
    var open by rememberSaveable{mutableStateOf(true)}
    LaunchedEffect(active){if(active)open=true}
    Column(Modifier.fillMaxSize().background(Bg)){
        Header(ui("Сканувати"),api.server!=null,hyperion,onHyperion,outputTarget,onOutputTarget,onMenu)
        Box(Modifier.weight(1f),contentAlignment=Alignment.Center){
            Button(onClick={open=true},shape=RoundedCornerShape(14.dp),colors=ButtonDefaults.buttonColors(containerColor=Accent),modifier=Modifier.fillMaxWidth(.72f).height(56.dp)){Mdi("scanner",22.dp,Color.White);Spacer(Modifier.width(9.dp));Text(ui("Відкрити сканер"),fontWeight=FontWeight.Bold)}
        }
        if(open&&api.server!=null)MfpDialog(api){open=false}
    }
}

@Composable private fun HomeEdgeScreen(api:HomeApi,hyperion:Boolean,onHyperion:(Boolean)->Unit,outputTarget:String,onOutputTarget:(String)->Unit,onMenu:()->Unit,onBack:()->Unit){
    val scope=rememberCoroutineScope();val context=LocalContext.current
    val healthAdapter=remember(context){SkeletonHealthConnectAdapter(context.applicationContext)}
    var healthAvailability by remember{mutableStateOf(SkeletonHealthConnectAdapter.availability(context))}
    var healthGranted by remember{mutableStateOf(false)}
    var healthStatus by remember{mutableStateOf("")}
    var healthBusy by remember{mutableStateOf(false)}
    var trackingEnabled by remember{mutableStateOf(LocationTrackingService.isTrackingEnabled(context))}
    var trackingBackgroundGranted by remember{mutableStateOf(LocationTrackingService.hasBackgroundLocation(context))}
    var trackingRestartNeeded by remember{mutableStateOf(LocationTrackingService.isRestartNeeded(context))}
    var trackingStatus by remember{mutableStateOf("")}
    var secretText by rememberSaveable{mutableStateOf("")};var gmailCallbackUrl by rememberSaveable{mutableStateOf("")};var gmailExpectedState by rememberSaveable{mutableStateOf("")};var status by remember{mutableStateOf("")};var busy by remember{mutableStateOf(false)};var gmailConfigured by remember{mutableStateOf(false)};var gmailAuthorized by remember{mutableStateOf(false)};var bitwardenBackup by remember{mutableStateOf("not_needed")}
    var telegramApiConfigured by remember{mutableStateOf(false)}
    var telegramSessionConfigured by remember{mutableStateOf(false)}
    var telegramAuthorized by remember{mutableStateOf(false)}
    var telegramSourceCount by remember{mutableStateOf(0)}
    var telegramLastSyncStatus by remember{mutableStateOf("")}
    var telegramApiId by remember{mutableStateOf("")}
    var telegramApiHash by remember{mutableStateOf("")}
    var telegramPhone by remember{mutableStateOf("")}
    var telegramCode by remember{mutableStateOf("")}
    var telegramPassword by remember{mutableStateOf("")}
    var telegramAuthStage by remember{mutableStateOf("")}
    var telegramSourceHandle by remember{mutableStateOf("")}
    var telegramSourcePrivate by remember{mutableStateOf(false)}
    var telegramSources by remember{mutableStateOf<List<String>>(emptyList())}
    var telegramStatus by remember{mutableStateOf("")}
    fun refresh(){scope.launch{
        runCatching{api.get("/api/native/home-edge/secrets/status")}.onSuccess{
            gmailConfigured=it.optBoolean("gmail_oauth_client_configured")
            gmailAuthorized=it.optBoolean("gmail_authorized")
            bitwardenBackup=it.optString("bitwarden_backup","not_needed")
            telegramApiConfigured=it.optBoolean("telegram_api_configured")
            telegramSessionConfigured=it.optBoolean("telegram_session_configured")
            telegramAuthorized=it.optBoolean("telegram_authorized")
            telegramSourceCount=it.optInt("telegram_source_count",0)
            telegramLastSyncStatus=it.optString("telegram_last_sync_status")
        }
        runCatching{api.get("/api/native/home-edge/telegram/sources")}.onSuccess{j->
            telegramSources=j.optJSONArray("sources")?.objects().orEmpty().mapNotNull{x->x.cleanText("handle").takeIf{h->h.isNotBlank()}}
        }
    }}
    suspend fun syncHealth(){
        healthBusy=true
        try{
            val summary=healthAdapter.readRecentSummary()?:throw IllegalStateException("Health Connect не надав дані")
            val receipt=api.post("/api/native/home-edge/android/health",summary.toSkeletonJson())
            healthStatus="Синхронізовано: сон — ${receipt.optInt("sleep_session_count",0)} сес., кроки — ${if(receipt.optBoolean("has_steps"))"так" else "немає"}"
        }catch(e:Exception){healthStatus=e.message?:"Не вдалося синхронізувати Health Connect"}
        finally{healthBusy=false}
    }
    fun refreshHealth(){scope.launch{
        healthAvailability=SkeletonHealthConnectAdapter.availability(context)
        healthGranted=runCatching{healthAdapter.hasReadPermissions()}.getOrDefault(false)
        if(healthGranted&&api.server!=null)syncHealth()
    }}
    LaunchedEffect(api.server){if(api.server!=null){refresh();refreshHealth()}}
    val healthPermissionLauncher=rememberLauncherForActivityResult(SkeletonHealthConnectAdapter.permissionContract()){granted->
        healthGranted=granted.containsAll(SkeletonHealthConnectAdapter.readPermissions)
        if(healthGranted&&api.server!=null)scope.launch{syncHealth()} else if(!healthGranted)healthStatus="Доступ до сну/кроків не надано"
    }
    val trackingPermissionLauncher=rememberLauncherForActivityResult(ActivityResultContracts.RequestMultiplePermissions()){grants->
        val granted=(grants[Manifest.permission.ACCESS_FINE_LOCATION]==true)||(grants[Manifest.permission.ACCESS_COARSE_LOCATION]==true)
        if(granted){
            LocationTrackingService.start(context)
            trackingEnabled=true
            trackingStatus="Тракінг увімкнено"
        }else{
            trackingEnabled=false
            trackingStatus="Доступ до місцезнаходження не надано"
        }
    }
    val trackingSettingsLauncher=rememberLauncherForActivityResult(ActivityResultContracts.StartActivityForResult()){
        trackingBackgroundGranted=LocationTrackingService.hasBackgroundLocation(context)
        trackingRestartNeeded=LocationTrackingService.isRestartNeeded(context)
        if(trackingEnabled&&trackingBackgroundGranted){
            runCatching{LocationTrackingService.start(context)}
                .onSuccess{trackingRestartNeeded=false;trackingStatus="Фоновий тракінг відновлено"}
                .onFailure{trackingStatus=it.message?:"Не вдалося відновити фоновий тракінг"}
        }else if(trackingEnabled&&!trackingBackgroundGranted){
            trackingStatus="Для відновлення після перезавантаження виберіть «Дозволити завжди» для місцезнаходження"
        }
    }
    val filePicker=rememberLauncherForActivityResult(ActivityResultContracts.OpenDocument()){uri->if(uri!=null){scope.launch{busy=true;status="Перевіряю файл…";runCatching{api.uploadSecretFile(context,uri)}.onSuccess{status="Секрет прийнято · ${it.optString("label","готово")}";refresh()}.onFailure{status=it.message?:"Не вдалося додати секрет"};busy=false}}}
    Column(Modifier.fillMaxSize().background(Bg)){
        Header("Home Edge",api.server!=null,hyperion,onHyperion,outputTarget,onOutputTarget,onMenu,subtitle="Керування вузлом")
        Row(Modifier.fillMaxWidth().padding(horizontal=12.dp,vertical=4.dp),verticalAlignment=Alignment.CenterVertically){TextButton(onClick=onBack){Mdi("chevron-left",18.dp,Muted);Spacer(Modifier.width(5.dp));Text("Пристрої",color=Muted)}}
        LazyColumn(Modifier.weight(1f).padding(horizontal=13.dp),contentPadding=PaddingValues(bottom=24.dp),verticalArrangement=Arrangement.spacedBy(12.dp)){
            item{Column(Modifier.fillMaxWidth().clip(RoundedCornerShape(16.dp)).background(Card).border(1.dp,Line,RoundedCornerShape(16.dp)).padding(16.dp)){
                Text("Здоров’я телефону",fontSize=17.sp,fontWeight=FontWeight.Bold,color=Text)
                val availabilityText=when(healthAvailability){HealthConnectAvailability.AVAILABLE->if(healthGranted)"Health Connect: доступ надано" else "Health Connect: потрібен дозвіл";HealthConnectAvailability.UPDATE_REQUIRED->"Health Connect: потрібно оновити";HealthConnectAvailability.UNAVAILABLE->"Health Connect: недоступний"}
                val availabilityColor=when{healthGranted->Green;healthAvailability==HealthConnectAvailability.AVAILABLE->Warn;else->Muted}
                Row(Modifier.padding(top=8.dp),verticalAlignment=Alignment.CenterVertically){Box(Modifier.size(8.dp).clip(CircleShape).background(availabilityColor));Spacer(Modifier.width(8.dp));Text(availabilityText,fontSize=11.sp,color=availabilityColor)}
                Text("Збираємо лише агреговані кроки та сесії/стадії сну. Сирі сенсорні потоки й інші медичні дані не читаються.",fontSize=10.sp,lineHeight=15.sp,color=Muted,modifier=Modifier.padding(top=8.dp))
                if(healthAvailability==HealthConnectAvailability.AVAILABLE&&!healthGranted){
                    Button(onClick={healthPermissionLauncher.launch(SkeletonHealthConnectAdapter.readPermissions)},enabled=!healthBusy,shape=RoundedCornerShape(12.dp),colors=ButtonDefaults.buttonColors(containerColor=Accent),modifier=Modifier.fillMaxWidth().height(48.dp).padding(top=10.dp)){Text("Надати доступ до сну та кроків",fontWeight=FontWeight.Bold,color=Color.White)}
                }else if(healthGranted){
                    Button(onClick={scope.launch{syncHealth()}},enabled=!healthBusy&&api.server!=null,shape=RoundedCornerShape(12.dp),colors=ButtonDefaults.buttonColors(containerColor=Action),modifier=Modifier.fillMaxWidth().height(46.dp).padding(top=10.dp)){Text(if(healthBusy)"Синхронізація…" else "Синхронізувати зараз",fontWeight=FontWeight.SemiBold,color=Text)}
                }
                if(healthStatus.isNotBlank())Text(healthStatus,fontSize=11.sp,lineHeight=16.sp,color=if(healthStatus.startsWith("Синхронізовано"))Green else Muted,modifier=Modifier.padding(top=9.dp))
            }}
            item{Column(Modifier.fillMaxWidth().clip(RoundedCornerShape(16.dp)).background(Card).border(1.dp,Line,RoundedCornerShape(16.dp)).padding(16.dp)){
                Row(Modifier.fillMaxWidth(),verticalAlignment=Alignment.CenterVertically){
                    Column(Modifier.weight(1f)){
                        Text("Тракінг",fontSize=17.sp,fontWeight=FontWeight.Bold,color=Text)
                        Text(if(trackingEnabled)"Активний · приватний Geo-контур" else "Вимкнено",fontSize=11.sp,color=if(trackingEnabled)Green else Muted,modifier=Modifier.padding(top=4.dp))
                    }
                    Switch(
                        checked=trackingEnabled,
                        onCheckedChange={enabled->
                            if(enabled){
                                val fine=ContextCompat.checkSelfPermission(context,Manifest.permission.ACCESS_FINE_LOCATION)==PackageManager.PERMISSION_GRANTED
                                val coarse=ContextCompat.checkSelfPermission(context,Manifest.permission.ACCESS_COARSE_LOCATION)==PackageManager.PERMISSION_GRANTED
                                if(fine||coarse){
                                    LocationTrackingService.start(context)
                                    trackingEnabled=true
                                    trackingStatus="Тракінг увімкнено"
                                }else{
                                    trackingPermissionLauncher.launch(arrayOf(Manifest.permission.ACCESS_FINE_LOCATION,Manifest.permission.ACCESS_COARSE_LOCATION))
                                }
                            }else{
                                LocationTrackingService.stop(context)
                                trackingEnabled=false
                                trackingStatus="Тракінг вимкнено"
                            }
                        }
                    )
                }
                Text("Під час роботи Home тримає foreground-service: не частіше ніж раз на 5 хв або після переміщення ≈100 м. Сирі координати зберігаються лише локально на Home Edge й не потрапляють у GitHub або MemoryGate.",fontSize=10.sp,lineHeight=15.sp,color=Muted,modifier=Modifier.padding(top=9.dp))
                Row(Modifier.padding(top=8.dp),verticalAlignment=Alignment.CenterVertically){
                    val bgColor=if(trackingBackgroundGranted)Green else Warn
                    Box(Modifier.size(8.dp).clip(CircleShape).background(bgColor))
                    Spacer(Modifier.width(8.dp))
                    Text(if(trackingBackgroundGranted)"Фоновий доступ: надано" else "Фоновий доступ: потрібен для reboot/update recovery",fontSize=10.sp,color=bgColor)
                }
                if(!trackingBackgroundGranted&&Build.VERSION.SDK_INT>=Build.VERSION_CODES.Q){
                    OutlinedButton(
                        onClick={
                            trackingSettingsLauncher.launch(
                                Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS,Uri.parse("package:"+context.packageName))
                            )
                        },
                        modifier=Modifier.fillMaxWidth().height(44.dp).padding(top=8.dp),
                        shape=RoundedCornerShape(12.dp),
                        border=BorderStroke(1.dp,Warn)
                    ){Text("Надати фоновий доступ до місцезнаходження",fontSize=11.sp,fontWeight=FontWeight.SemiBold,color=Warn)}
                    Text("У Android: Дозволи → Місцезнаходження → Дозволити завжди.",fontSize=9.sp,lineHeight=13.sp,color=Muted,modifier=Modifier.padding(top=5.dp))
                }
                if(trackingRestartNeeded)Text("Після оновлення/перезавантаження tracker потребує відновлення.",fontSize=10.sp,color=Warn,modifier=Modifier.padding(top=7.dp))
                val buffered=LocationTrackingService.bufferedCount(context)
                val lastObserved=LocationTrackingService.lastObservedAt(context)
                if(buffered>0)Text("Очікують відправлення: $buffered точ.",fontSize=10.sp,color=Warn,modifier=Modifier.padding(top=7.dp))
                if(lastObserved.isNotBlank())Text("Остання точка: $lastObserved",fontSize=10.sp,color=Muted,modifier=Modifier.padding(top=5.dp))
                if(trackingStatus.isNotBlank())Text(trackingStatus,fontSize=11.sp,color=if(trackingEnabled)Green else Muted,modifier=Modifier.padding(top=7.dp))
            }}
            item{Column(Modifier.fillMaxWidth().clip(RoundedCornerShape(16.dp)).background(Card).padding(16.dp)){
                Text("Секрети",fontSize=17.sp,fontWeight=FontWeight.Bold,color=Text)
                Text("Gmail і Telegram. Довготривалі секрети зберігаються через Bitwarden; одноразові коди не записуються в Home.",fontSize=11.sp,lineHeight=16.sp,color=Muted,modifier=Modifier.padding(top=5.dp,bottom=12.dp))
                Row(verticalAlignment=Alignment.CenterVertically){Box(Modifier.size(8.dp).clip(CircleShape).background(if(gmailAuthorized)Green else if(gmailConfigured)Warn else Muted));Spacer(Modifier.width(8.dp));Text(when{gmailAuthorized->"Gmail: авторизовано";gmailConfigured->"Gmail: секрет завантажено, потрібна авторизація";else->"Gmail: секрет ще не додано"},fontSize=11.sp,color=if(gmailAuthorized)Green else if(gmailConfigured)Warn else Muted)}
                if(gmailConfigured){Text(if(bitwardenBackup=="stored")"Bitwarden: резервну копію збережено" else "Bitwarden: очікує синхронізації",fontSize=11.sp,color=if(bitwardenBackup=="stored")Green else Warn,modifier=Modifier.padding(top=7.dp))}
                if(gmailConfigured&&!gmailAuthorized){
                    Button(onClick={scope.launch{busy=true;status="Готую авторизацію на телефоні…";runCatching{api.post("/api/native/home-edge/gmail/authorize",JSONObject())}.onSuccess{start->if(start.optBoolean("gmail_authorized")){status="Gmail авторизовано";refresh()}else{val authUrl=start.optString("authorization_url");gmailExpectedState=start.optString("state");if(authUrl.isBlank()||gmailExpectedState.isBlank())throw IllegalStateException("Не отримано посилання Google");context.startActivity(Intent(Intent.ACTION_VIEW,Uri.parse(authUrl)).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK));status="Після Google скопіюйте повну адресу 127.0.0.1 з браузера у поле нижче."}}.onFailure{status=it.message?:"Не вдалося почати авторизацію Gmail"};busy=false}},enabled=!busy,shape=RoundedCornerShape(12.dp),colors=ButtonDefaults.buttonColors(containerColor=Action),modifier=Modifier.fillMaxWidth().height(48.dp).padding(top=10.dp)){Text("Авторизувати Gmail (телефон)",fontWeight=FontWeight.Bold,color=Text)}
                    Text("Після підтвердження Google браузер може показати ERR_CONNECTION_REFUSED — це нормально. Скопіюйте повну адресу сторінки 127.0.0.1:53682 з адресного рядка.",fontSize=10.sp,lineHeight=15.sp,color=Muted,modifier=Modifier.padding(top=10.dp,bottom=6.dp))
                    OutlinedTextField(value=gmailCallbackUrl,onValueChange={if(it.length<=8192)gmailCallbackUrl=it},enabled=!busy,label={Text("Адреса після Google")},placeholder={Text("http://127.0.0.1:53682/?state=…&code=…")},minLines=2,maxLines=4,modifier=Modifier.fillMaxWidth(),colors=OutlinedTextFieldDefaults.colors(focusedTextColor=Text,unfocusedTextColor=Text,focusedBorderColor=Accent,unfocusedBorderColor=Line,focusedLabelColor=Accent,unfocusedLabelColor=Muted,cursorColor=Accent))
                    Button(onClick={scope.launch{busy=true;status="Завершую авторизацію Gmail…";runCatching{val cb=Uri.parse(gmailCallbackUrl.trim());if(cb.host!="127.0.0.1"||cb.port!=53682)throw IllegalStateException("Вставте повну адресу 127.0.0.1:53682 з браузера");val code=cb.getQueryParameter("code").orEmpty();val state=cb.getQueryParameter("state").orEmpty();if(code.isBlank()||state.isBlank())throw IllegalStateException("У адресі Google немає code/state");if(gmailExpectedState.isNotBlank()&&state!=gmailExpectedState)throw IllegalStateException("Ця адреса належить іншій сесії авторизації");api.post("/api/native/home-edge/gmail/callback",JSONObject().put("code",code).put("state",state))}.onSuccess{done->status=done.optString("message","Gmail авторизовано");gmailCallbackUrl="";gmailExpectedState="";refresh()}.onFailure{status=it.message?:"Не вдалося завершити авторизацію Gmail"};busy=false}},enabled=!busy&&gmailCallbackUrl.isNotBlank(),shape=RoundedCornerShape(12.dp),colors=ButtonDefaults.buttonColors(containerColor=Accent),modifier=Modifier.fillMaxWidth().height(48.dp).padding(top=8.dp)){Text("Завершити авторизацію",fontWeight=FontWeight.Bold,color=Color.White)}
                }

                HorizontalDivider(Modifier.padding(vertical=14.dp),color=Line)
                Text("Telegram · читання каналів",fontSize=15.sp,fontWeight=FontWeight.Bold,color=Text)
                val tgColor=when{telegramAuthorized->Green;telegramApiConfigured->Warn;else->Muted}
                val tgLabel=when{
                    telegramAuthorized->"Telegram: MTProto авторизовано"
                    telegramSessionConfigured->"Telegram: сесія збережена, потрібна перевірка"
                    telegramApiConfigured->"Telegram: API налаштовано, потрібна авторизація"
                    else->"Telegram: API ще не налаштовано"
                }
                Row(Modifier.padding(top=8.dp),verticalAlignment=Alignment.CenterVertically){Box(Modifier.size(8.dp).clip(CircleShape).background(tgColor));Spacer(Modifier.width(8.dp));Text(tgLabel,fontSize=11.sp,color=tgColor)}
                if(telegramSourceCount>0||telegramLastSyncStatus.isNotBlank())Text("Джерел: $telegramSourceCount · sync: ${telegramLastSyncStatus.ifBlank{"ще не запускався"}}",fontSize=10.sp,color=Muted,modifier=Modifier.padding(top=6.dp))
                Text("User-account використовується тільки для READ_PUBLIC / explicit allowlist. Надсилання, редагування, видалення, реакції та join/leave через MTProto відсутні.",fontSize=10.sp,lineHeight=15.sp,color=Muted,modifier=Modifier.padding(top=8.dp))

                if(!telegramApiConfigured){
                    OutlinedTextField(value=telegramApiId,onValueChange={v->if(v.length<=20&&v.all{it.isDigit()})telegramApiId=v},enabled=!busy,label={Text("Telegram API ID")},singleLine=true,modifier=Modifier.fillMaxWidth().padding(top=9.dp),colors=OutlinedTextFieldDefaults.colors(focusedTextColor=Text,unfocusedTextColor=Text,focusedBorderColor=Accent,unfocusedBorderColor=Line,focusedLabelColor=Accent,unfocusedLabelColor=Muted,cursorColor=Accent))
                    OutlinedTextField(value=telegramApiHash,onValueChange={v->if(v.length<=64)telegramApiHash=v.trim()},enabled=!busy,label={Text("Telegram API hash")},singleLine=true,visualTransformation=PasswordVisualTransformation(),modifier=Modifier.fillMaxWidth().padding(top=7.dp),colors=OutlinedTextFieldDefaults.colors(focusedTextColor=Text,unfocusedTextColor=Text,focusedBorderColor=Accent,unfocusedBorderColor=Line,focusedLabelColor=Accent,unfocusedLabelColor=Muted,cursorColor=Accent))
                    Button(onClick={scope.launch{
                        busy=true;telegramStatus="Зберігаю Telegram API у Bitwarden…"
                        val value=JSONObject().put("kind","telegram_mtproto").put("api_id",telegramApiId).put("api_hash",telegramApiHash).toString()
                        runCatching{api.post("/api/native/home-edge/secrets/text",JSONObject().put("label","Telegram MTProto API").put("value",value))}
                            .onSuccess{telegramApiId="";telegramApiHash="";telegramStatus="Telegram API збережено";refresh()}
                            .onFailure{telegramStatus=it.message?:"Не вдалося зберегти Telegram API"}
                        busy=false
                    }},enabled=!busy&&telegramApiId.length>=5&&telegramApiHash.length==32,shape=RoundedCornerShape(12.dp),colors=ButtonDefaults.buttonColors(containerColor=Accent),modifier=Modifier.fillMaxWidth().height(48.dp).padding(top=9.dp)){Text("Зберегти Telegram API",fontWeight=FontWeight.Bold,color=Color.White)}
                }else if(!telegramAuthorized){
                    OutlinedTextField(value=telegramPhone,onValueChange={if(it.length<=32)telegramPhone=it},enabled=!busy&&telegramAuthStage.isBlank(),label={Text("Номер Telegram")},placeholder={Text("+49…")},singleLine=true,modifier=Modifier.fillMaxWidth().padding(top=9.dp),colors=OutlinedTextFieldDefaults.colors(focusedTextColor=Text,unfocusedTextColor=Text,focusedBorderColor=Accent,unfocusedBorderColor=Line,focusedLabelColor=Accent,unfocusedLabelColor=Muted,cursorColor=Accent))
                    if(telegramAuthStage.isBlank()){
                        Button(onClick={scope.launch{
                            busy=true;telegramStatus="Надсилаю запит Telegram…"
                            runCatching{api.post("/api/native/home-edge/telegram/auth/start",JSONObject().put("phone",telegramPhone.trim()))}
                                .onSuccess{telegramPhone="";telegramAuthStage="code";telegramStatus=it.optString("message","Код Telegram надіслано")}
                                .onFailure{telegramStatus=it.message?:"Не вдалося почати Telegram авторизацію"}
                            busy=false
                        }},enabled=!busy&&telegramPhone.isNotBlank(),shape=RoundedCornerShape(12.dp),colors=ButtonDefaults.buttonColors(containerColor=Action),modifier=Modifier.fillMaxWidth().height(46.dp).padding(top=8.dp)){Text("Авторизувати Telegram",fontWeight=FontWeight.SemiBold,color=Text)}
                    }
                    if(telegramAuthStage=="code"){
                        OutlinedTextField(value=telegramCode,onValueChange={v->if(v.length<=10&&v.all{it.isDigit()})telegramCode=v},enabled=!busy,label={Text("Одноразовий код Telegram")},singleLine=true,visualTransformation=PasswordVisualTransformation(),modifier=Modifier.fillMaxWidth().padding(top=8.dp),colors=OutlinedTextFieldDefaults.colors(focusedTextColor=Text,unfocusedTextColor=Text,focusedBorderColor=Accent,unfocusedBorderColor=Line,focusedLabelColor=Accent,unfocusedLabelColor=Muted,cursorColor=Accent))
                        Button(onClick={scope.launch{
                            busy=true
                            val code=telegramCode;telegramCode=""
                            runCatching{api.post("/api/native/home-edge/telegram/auth/code",JSONObject().put("code",code))}
                                .onSuccess{done->if(done.optString("status")=="PASSWORD_REQUIRED"){telegramAuthStage="password";telegramStatus=done.optString("message","Потрібен 2FA пароль")}else{telegramAuthStage="";telegramStatus="Telegram авторизовано";refresh()}}
                                .onFailure{telegramStatus=it.message?:"Telegram не прийняв код"}
                            busy=false
                        }},enabled=!busy&&telegramCode.length>=3,shape=RoundedCornerShape(12.dp),colors=ButtonDefaults.buttonColors(containerColor=Accent),modifier=Modifier.fillMaxWidth().height(46.dp).padding(top=8.dp)){Text("Підтвердити код",fontWeight=FontWeight.Bold,color=Color.White)}
                    }
                    if(telegramAuthStage=="password"){
                        OutlinedTextField(value=telegramPassword,onValueChange={if(it.length<=512)telegramPassword=it},enabled=!busy,label={Text("Пароль двоетапної перевірки")},singleLine=true,visualTransformation=PasswordVisualTransformation(),modifier=Modifier.fillMaxWidth().padding(top=8.dp),colors=OutlinedTextFieldDefaults.colors(focusedTextColor=Text,unfocusedTextColor=Text,focusedBorderColor=Accent,unfocusedBorderColor=Line,focusedLabelColor=Accent,unfocusedLabelColor=Muted,cursorColor=Accent))
                        Button(onClick={scope.launch{
                            busy=true
                            val password=telegramPassword;telegramPassword=""
                            runCatching{api.post("/api/native/home-edge/telegram/auth/password",JSONObject().put("password",password))}
                                .onSuccess{telegramAuthStage="";telegramStatus="Telegram авторизовано";refresh()}
                                .onFailure{telegramStatus=it.message?:"Telegram не прийняв 2FA пароль"}
                            busy=false
                        }},enabled=!busy&&telegramPassword.isNotBlank(),shape=RoundedCornerShape(12.dp),colors=ButtonDefaults.buttonColors(containerColor=Accent),modifier=Modifier.fillMaxWidth().height(46.dp).padding(top=8.dp)){Text("Підтвердити 2FA",fontWeight=FontWeight.Bold,color=Color.White)}
                    }
                }else{
                    OutlinedTextField(value=telegramSourceHandle,onValueChange={if(it.length<=65)telegramSourceHandle=it.trim()},enabled=!busy,label={Text("Канал")},placeholder={Text("@channel")},singleLine=true,modifier=Modifier.fillMaxWidth().padding(top=9.dp),colors=OutlinedTextFieldDefaults.colors(focusedTextColor=Text,unfocusedTextColor=Text,focusedBorderColor=Accent,unfocusedBorderColor=Line,focusedLabelColor=Accent,unfocusedLabelColor=Muted,cursorColor=Accent))
                    Row(Modifier.fillMaxWidth().padding(top=5.dp),verticalAlignment=Alignment.CenterVertically){Switch(checked=telegramSourcePrivate,onCheckedChange={telegramSourcePrivate=it},enabled=!busy);Spacer(Modifier.width(8.dp));Text(if(telegramSourcePrivate)"Приватний allowlisted канал" else "Публічний канал",fontSize=10.sp,color=Muted)}
                    Button(onClick={scope.launch{
                        busy=true
                        runCatching{api.post("/api/native/home-edge/telegram/sources",JSONObject().put("handle",telegramSourceHandle).put("private",telegramSourcePrivate))}
                            .onSuccess{telegramStatus="Канал додано";telegramSourceHandle="";telegramSourcePrivate=false;refresh()}
                            .onFailure{telegramStatus=it.message?:"Не вдалося додати канал"}
                        busy=false
                    }},enabled=!busy&&telegramSourceHandle.startsWith("@")&&telegramSourceHandle.length>=6,shape=RoundedCornerShape(12.dp),colors=ButtonDefaults.buttonColors(containerColor=Action),modifier=Modifier.fillMaxWidth().height(46.dp).padding(top=7.dp)){Text("Додати канал",fontWeight=FontWeight.SemiBold,color=Text)}
                    if(telegramSources.isNotEmpty())Text("Allowlist: "+telegramSources.joinToString(" · "),fontSize=10.sp,lineHeight=15.sp,color=Muted,modifier=Modifier.padding(top=8.dp))
                    Button(onClick={scope.launch{
                        busy=true;telegramStatus="Синхронізую Telegram…"
                        runCatching{api.post("/api/native/home-edge/telegram/sync",JSONObject())}
                            .onSuccess{done->telegramStatus="Sync: "+done.optString("status","готово");refresh()}
                            .onFailure{telegramStatus=it.message?:"Telegram sync не вдався"}
                        busy=false
                    }},enabled=!busy&&telegramSourceCount>0,shape=RoundedCornerShape(12.dp),colors=ButtonDefaults.buttonColors(containerColor=Accent),modifier=Modifier.fillMaxWidth().height(46.dp).padding(top=8.dp)){Text("Синхронізувати Telegram",fontWeight=FontWeight.Bold,color=Color.White)}
                }
                if(telegramStatus.isNotBlank())Text(telegramStatus,fontSize=11.sp,lineHeight=16.sp,color=if(telegramAuthorized||telegramStatus.contains("збережено")||telegramStatus.contains("додано"))Green else Muted,modifier=Modifier.padding(top=9.dp))
                Button(onClick={filePicker.launch(arrayOf("*/*"))},enabled=!busy,shape=RoundedCornerShape(12.dp),colors=ButtonDefaults.buttonColors(containerColor=Accent),modifier=Modifier.fillMaxWidth().height(48.dp).padding(top=10.dp)){Text("Завантажити секрет з файлу",fontWeight=FontWeight.Bold,color=Color.White)}
                Text("або вставити вручну",fontSize=10.sp,color=Muted,modifier=Modifier.padding(top=14.dp,bottom=6.dp))
                OutlinedTextField(value=secretText,onValueChange={if(it.length<=131072)secretText=it},enabled=!busy,label={Text("JSON / секрет")},placeholder={Text("Вставте вміст сюди")},minLines=5,maxLines=10,modifier=Modifier.fillMaxWidth(),colors=OutlinedTextFieldDefaults.colors(focusedTextColor=Text,unfocusedTextColor=Text,focusedBorderColor=Accent,unfocusedBorderColor=Line,focusedLabelColor=Accent,unfocusedLabelColor=Muted,cursorColor=Accent))
                Button(onClick={scope.launch{busy=true;status="Перевіряю…";runCatching{api.post("/api/native/home-edge/secrets/text",JSONObject().put("label","Home app input").put("value",secretText))}.onSuccess{status="Секрет прийнято · ${it.optString("label","готово")}";secretText="";refresh()}.onFailure{status=it.message?:"Не вдалося додати секрет"};busy=false}},enabled=!busy&&secretText.isNotBlank(),shape=RoundedCornerShape(12.dp),colors=ButtonDefaults.buttonColors(containerColor=Action),modifier=Modifier.fillMaxWidth().height(46.dp).padding(top=8.dp)){Text("Додати введений секрет",fontWeight=FontWeight.SemiBold,color=Text)}
                if(status.isNotBlank())Text(status,fontSize=11.sp,lineHeight=16.sp,color=if(status.startsWith("Секрет прийнято"))Green else Muted,modifier=Modifier.padding(top=10.dp))
                Text("Розпізнаються Google OAuth Desktop JSON та Telegram MTProto API JSON. Самі значення секретів у відповідях і журналах не показуються.",fontSize=10.sp,lineHeight=15.sp,color=Muted,modifier=Modifier.padding(top=12.dp))
            }}
        }
    }
}


private const val DEVICE_ORDER_PREF="device_display_order"
private fun deviceOrderIds(prefs:android.content.SharedPreferences)=prefs.getString(DEVICE_ORDER_PREF,"").orEmpty().split('|').filter{it.isNotBlank()}
private fun orderNativeDevices(items:List<NativeDeviceUi>,prefs:android.content.SharedPreferences):List<NativeDeviceUi>{val r=deviceOrderIds(prefs).withIndex().associate{it.value to it.index};return items.sortedWith(compareBy<NativeDeviceUi>{r[it.id]?:Int.MAX_VALUE}.thenBy{deviceDomainOrder.indexOf(it.domain).let{x->if(x<0)999 else x}}.thenBy{it.name})}
@Composable private fun DeviceOrderDialog(api:HomeApi,prefs:android.content.SharedPreferences,onDismiss:()->Unit){
 val context=LocalContext.current;var ids by remember{mutableStateOf(deviceOrderIds(prefs))};var names by remember{mutableStateOf<Map<String,String>>(emptyMap())}
 LaunchedEffect(api.server){if(api.server!=null)runCatching{api.get("/api/native/devices")}.onSuccess{j->val rows=j.optJSONArray("items")?.objects().orEmpty();names=rows.associate{it.cleanText("device_id") to it.cleanText("name",it.cleanText("device_id"))};val all=rows.map{it.cleanText("device_id")}.filter{it.isNotBlank()};ids=(ids.filter{it in all}+all.filter{it !in ids}).distinct()}}
 Dialog(onDismissRequest=onDismiss){Column(Modifier.fillMaxWidth().clip(RoundedCornerShape(18.dp)).background(Card).padding(16.dp)){Text(ui("Порядок пристроїв"),fontSize=20.sp,fontWeight=FontWeight.Bold,color=Text);Text(ui("Стрілками змініть порядок."),fontSize=11.sp,color=Muted,modifier=Modifier.padding(top=4.dp,bottom=10.dp));LazyColumn(Modifier.heightIn(max=500.dp)){items(ids.size,key={ids[it]}){i->val id=ids[i];Row(Modifier.fillMaxWidth().padding(vertical=5.dp),verticalAlignment=Alignment.CenterVertically){Text(names[id]?:id,Modifier.weight(1f),fontSize=14.sp,color=Text,maxLines=2,overflow=TextOverflow.Ellipsis);IconButton(onClick={if(i>0){val m=ids.toMutableList();val x=m.removeAt(i);m.add(i-1,x);ids=m}},enabled=i>0){Mdi("chevron-up",22.dp,if(i>0)Text else Muted)};IconButton(onClick={if(i<ids.lastIndex){val m=ids.toMutableList();val x=m.removeAt(i);m.add(i+1,x);ids=m}},enabled=i<ids.lastIndex){Mdi("chevron-down",22.dp,if(i<ids.lastIndex)Text else Muted)}}}};Row(Modifier.fillMaxWidth().padding(top=12.dp),horizontalArrangement=Arrangement.End){TextButton(onClick=onDismiss){Text(ui("Скасувати"),color=Muted)};Spacer(Modifier.width(8.dp));Button(onClick={prefs.edit().putString(DEVICE_ORDER_PREF,ids.joinToString("|")).apply();Toast.makeText(context,"Порядок пристроїв збережено",Toast.LENGTH_SHORT).show();onDismiss()},colors=ButtonDefaults.buttonColors(containerColor=Accent)){Text(ui("Зберегти"),color=Color.White,fontWeight=FontWeight.Bold)}}}}
}

@Composable private fun DevicesScreen(api:HomeApi,active:Boolean,hyperion:Boolean,onHyperion:(Boolean)->Unit,outputTarget:String,onOutputTarget:(String)->Unit,onMenu:()->Unit){
    var sharpRemoteOpen by rememberSaveable{mutableStateOf(false)}; var mfpOpen by rememberSaveable{mutableStateOf(false)}; var homeEdgeOpen by rememberSaveable{mutableStateOf(false)}; var devices by remember{mutableStateOf(emptyList<NativeDeviceUi>())}; var loaded by remember{mutableStateOf(false)}
    val context=LocalContext.current; val devicePrefs=remember(context){context.getSharedPreferences("home_ui",Context.MODE_PRIVATE)}
    LaunchedEffect(Unit){if(deviceOrderIds(devicePrefs).isEmpty())devicePrefs.edit().putString(DEVICE_ORDER_PREF,"brother_printer").apply()}
    LaunchedEffect(api.server,active){if(api.server!=null){suspend fun pull():Boolean{var ok=false;runCatching{api.get("/api/native/devices")}.onSuccess{j->devices=j.optJSONArray("items")?.objects().orEmpty().map{d->val id=d.cleanText("device_id");val role=d.cleanText("role");val adapter=d.cleanText("adapter");val location=d.cleanText("location");val deps=d.optJSONArray("dependency_names")?.strings().orEmpty();val controller=d.cleanText("controller_name",d.cleanText("controller"));NativeDeviceUi(id,d.cleanText("name",id),role,adapter,d.optBoolean("online"),location,deps,controller,nativeDeviceDomain(id,role,location),nativeDeviceConnection(id,role,adapter,location,deps,controller))};loaded=true;ok=true};return ok};while(!loaded){if(pull())break;delay(1500)};if(active)while(true){delay(15000);pull()}}}
    if(homeEdgeOpen){HomeEdgeScreen(api,hyperion,onHyperion,outputTarget,onOutputTarget,onMenu){homeEdgeOpen=false};return}
    Column(Modifier.fillMaxSize().background(Bg)){
        Header(ui("Пристрої"),api.server!=null,hyperion,onHyperion,outputTarget,onOutputTarget,onMenu)
        LazyColumn(Modifier.weight(1f).padding(horizontal=13.dp),contentPadding=PaddingValues(top=6.dp,bottom=18.dp),verticalArrangement=Arrangement.spacedBy(10.dp)){
            if(!loaded)item{SimpleSection("Домашня схема","Читаю актуальний список пристроїв…")}
            val ordered=orderNativeDevices(devices,devicePrefs);ordered.map{it.domain}.distinct().forEach{domain->val group=ordered.filter{it.domain==domain};if(group.isNotEmpty())item(key=domain){DeviceDomainCard(domain,group,{sharpRemoteOpen=true},{mfpOpen=true},{homeEdgeOpen=true})}}
        }
        if(mfpOpen&&api.server!=null)MfpDialog(api){mfpOpen=false}
        if(sharpRemoteOpen&&api.server!=null)SharpRemoteDialog("${api.server}/ir/sharp"){sharpRemoteOpen=false}
    }
}


private data class SkItem(val icon:String,val title:String,val level:String,val text:String)
private data class SkSection(val id:String,val title:String,val items:List<SkItem>,val empty:String)
private data class SkDomain(val title:String,val level:String,val status:String,val text:String)
private data class SkWorkItem(val id:String,val title:String,val stage:String,val status:String,val blocked:Boolean)
private data class SkWorkFlow(val stages:List<String>,val items:List<SkWorkItem>)
private data class SkTimelineEvent(val at:Long,val title:String,val text:String,val source:String)
private data class SkHeatRow(val title:String,val status:String,val level:String,val freshness:String,val stale:Boolean)
private data class SkLiveData(val overall:String,val summaryTitle:String,val summaryText:String,val sections:List<SkSection>,val nextMilestone:String,val recent:List<String>,val domains:List<SkDomain>,val timeline:List<SkTimelineEvent>,val heatmap:List<SkHeatRow>,val views:List<String>,val updatedAt:Long,val workFlow:SkWorkFlow=SkWorkFlow(emptyList(),emptyList()))
private fun parseSkItem(j:JSONObject)=SkItem(j.cleanText("icon","information-outline"),j.cleanText("title","Стан"),j.cleanText("level","muted"),j.cleanText("text"))
private fun parseSkLive(j:JSONObject):SkLiveData{
    val sections=j.optJSONArray("sections")?.objects().orEmpty().map{x->SkSection(x.cleanText("id"),x.cleanText("title"),x.optJSONArray("items")?.objects().orEmpty().map(::parseSkItem),x.cleanText("empty","Немає активних пунктів."))}
    val domains=j.optJSONArray("domains")?.objects().orEmpty().map{x->SkDomain(x.cleanText("title"),x.cleanText("level"),x.cleanText("status"),x.cleanText("text"))}
    val recent=j.optJSONArray("recent_changes")?.let{a->(0 until a.length()).mapNotNull{idx->when(val v=a.opt(idx)){is JSONObject->v.cleanText("text").takeIf{it.isNotBlank()};else->a.optString(idx).takeIf{it.isNotBlank()}}}}.orEmpty()
    val timeline=j.optJSONArray("timeline")?.objects().orEmpty().map{x->SkTimelineEvent(x.optLong("at",0L),x.cleanText("title"),x.cleanText("text"),x.cleanText("source"))}.filter{it.title.isNotBlank()||it.text.isNotBlank()}
    val heatmap=j.optJSONArray("heatmap")?.objects().orEmpty().map{x->SkHeatRow(x.cleanText("title"),x.cleanText("status"),x.cleanText("level"),x.cleanText("freshness"),x.optBoolean("stale",true))}
    val views=j.optJSONArray("views")?.strings().orEmpty()
    val wf=j.optJSONObject("work_flow");val wfStages=wf?.optJSONArray("stages")?.strings().orEmpty();val wfItems=wf?.optJSONArray("items")?.objects().orEmpty().map{x->SkWorkItem(x.cleanText("id"),x.cleanText("title"),x.cleanText("stage"),x.cleanText("status"),x.optBoolean("blocked",false))}
    val summary=j.optJSONObject("summary")?:JSONObject()
    return SkLiveData(j.cleanText("overall","attention"),summary.cleanText("title","Читаю стан Skeleton…"),summary.cleanText("text","Дані оновлюються автоматично."),sections,j.cleanText("next_milestone"),recent,domains,timeline,heatmap,views,j.optLong("updated_at",0L),SkWorkFlow(wfStages,wfItems))
}
private fun skLevelColor(level:String)=when(level){"ok","healthy"->Green;"attention"->Warn;"building"->Purple;"waiting"->Color(0xFF77A7D9);else->Muted}
private fun skAgeLabel(updatedAt:Long):String{if(updatedAt<=0)return "оновлюється";val sec=((System.currentTimeMillis()/1000L)-updatedAt).coerceAtLeast(0);return when{sec<45->"щойно";sec<120->"хвилину тому";sec<3600->"${sec/60} хв тому";else->"${sec/3600} год тому"}}
@Composable private fun SkItemRow(item:SkItem){val c=skLevelColor(item.level);Row(Modifier.fillMaxWidth().padding(vertical=7.dp),verticalAlignment=Alignment.Top){Box(Modifier.size(34.dp).clip(RoundedCornerShape(10.dp)).background(c.copy(alpha=.10f)),contentAlignment=Alignment.Center){Mdi(item.icon,20.dp,c)};Column(Modifier.weight(1f).padding(start=10.dp)){Text(item.title,fontSize=13.sp,fontWeight=FontWeight.SemiBold,color=Text);if(item.text.isNotBlank())Text(item.text,fontSize=10.sp,lineHeight=15.sp,color=Muted,modifier=Modifier.padding(top=3.dp))}}}
@Composable private fun SkSectionCard(section:SkSection){Column(Modifier.fillMaxWidth().clip(RoundedCornerShape(16.dp)).background(Card).border(1.dp,Line,RoundedCornerShape(16.dp)).padding(horizontal=14.dp,vertical=12.dp)){Text(section.title,fontSize=15.sp,fontWeight=FontWeight.Bold,color=Text);if(section.items.isEmpty())Text(section.empty,fontSize=11.sp,lineHeight=16.sp,color=Muted,modifier=Modifier.padding(top=8.dp))else section.items.forEach{SkItemRow(it)}}}
@Composable private fun SkDomainCard(domain:SkDomain){val c=skLevelColor(domain.level);Row(Modifier.fillMaxWidth().heightIn(min=76.dp).clip(RoundedCornerShape(16.dp)).background(Card).border(1.dp,Line,RoundedCornerShape(16.dp)).padding(horizontal=14.dp,vertical=11.dp),verticalAlignment=Alignment.CenterVertically){Box(Modifier.size(9.dp).clip(CircleShape).background(c));Column(Modifier.weight(1f).padding(start=10.dp)){Row(verticalAlignment=Alignment.CenterVertically){Text(domain.title,fontSize=14.sp,fontWeight=FontWeight.SemiBold,color=Text,modifier=Modifier.weight(1f));Text(domain.status,fontSize=9.sp,color=c)};Text(domain.text,fontSize=10.sp,lineHeight=14.sp,color=Muted,modifier=Modifier.padding(top=5.dp),maxLines=2,overflow=TextOverflow.Ellipsis)}}}

private fun skEventAge(at:Long):String{if(at<=0)return "";val sec=((System.currentTimeMillis()/1000L)-at).coerceAtLeast(0);return when{sec<60->"щойно";sec<3600->"${sec/60} хв тому";sec<86400->"${sec/3600} год тому";else->"${sec/86400} дн тому"}}
@Composable private fun SkViewTabs(selected:String,onSelect:(String)->Unit,available:List<String>){val tabs=listOf("state" to "Стан","timeline" to "Timeline","heatmap" to "Heatmap");Row(Modifier.fillMaxWidth().clip(RoundedCornerShape(13.dp)).background(Card).padding(4.dp)){tabs.filter{available.isEmpty()||it.first in available}.forEach{(id,label)->val active=selected==id;Box(Modifier.weight(1f).clip(RoundedCornerShape(10.dp)).background(if(active)Action else Color.Transparent).clickable{onSelect(id)}.padding(vertical=9.dp),contentAlignment=Alignment.Center){Text(label,fontSize=11.sp,fontWeight=if(active)FontWeight.Bold else FontWeight.Medium,color=if(active)Text else Muted)}}}}
@Composable private fun SkTimelineCard(events:List<SkTimelineEvent>){Column(Modifier.fillMaxWidth().clip(RoundedCornerShape(16.dp)).background(Card).border(1.dp,Line,RoundedCornerShape(16.dp)).padding(14.dp)){Text("Що відбувається",fontSize=15.sp,fontWeight=FontWeight.Bold,color=Text);if(events.isEmpty())Text("Поки немає підтверджених подій.",fontSize=11.sp,color=Muted,modifier=Modifier.padding(top=9.dp))else events.take(20).forEachIndexed{idx,e->Row(Modifier.fillMaxWidth().padding(top=if(idx==0)10.dp else 7.dp),verticalAlignment=Alignment.Top){Column(horizontalAlignment=Alignment.CenterHorizontally){Box(Modifier.size(9.dp).clip(CircleShape).background(Accent));if(idx<events.lastIndex)Box(Modifier.width(1.dp).height(35.dp).background(Line))};Column(Modifier.weight(1f).padding(start=10.dp)){Row(Modifier.fillMaxWidth()){Text(e.title,fontSize=12.sp,fontWeight=FontWeight.SemiBold,color=Text,modifier=Modifier.weight(1f));Text(skEventAge(e.at),fontSize=9.sp,color=Muted)};if(e.text.isNotBlank())Text(e.text,fontSize=10.sp,lineHeight=14.sp,color=Muted,modifier=Modifier.padding(top=2.dp),maxLines=3,overflow=TextOverflow.Ellipsis)}}}}}

@Composable private fun SkHeatmap(rows:List<SkHeatRow>){Column(Modifier.fillMaxWidth().clip(RoundedCornerShape(16.dp)).background(Card).border(1.dp,Line,RoundedCornerShape(16.dp)).padding(14.dp)){Text("Здоров’я системи",fontSize=15.sp,fontWeight=FontWeight.Bold,color=Text);Text("Один погляд на домени. Сіре означає: свіжість не підтверджена.",fontSize=10.sp,lineHeight=14.sp,color=Muted,modifier=Modifier.padding(top=4.dp,bottom=8.dp));if(rows.isEmpty())Text("Немає даних.",fontSize=11.sp,color=Muted)else rows.forEach{r->val c=if(r.stale)Muted else skLevelColor(r.level);Row(Modifier.fillMaxWidth().padding(vertical=5.dp),verticalAlignment=Alignment.CenterVertically){Text(r.title,fontSize=11.sp,color=Text,modifier=Modifier.weight(1f));Box(Modifier.width(72.dp).height(22.dp).clip(RoundedCornerShape(7.dp)).background(c.copy(alpha=.16f)),contentAlignment=Alignment.Center){Text(if(r.stale)"STALE" else r.status,fontSize=8.sp,fontWeight=FontWeight.Bold,color=c)}}}}}

@Composable private fun SkWorkFlowViz(flow:SkWorkFlow){
 val stages=if(flow.stages.isEmpty())listOf("Ідея","Черга","Виконується","Перевірка","Готово","Підключено") else flow.stages;var selected by rememberSaveable{mutableStateOf("")};val active=flow.items.filter{selected.isBlank()||it.stage==selected}
 Column(Modifier.fillMaxWidth().clip(RoundedCornerShape(16.dp)).background(Card).border(1.dp,Line,RoundedCornerShape(16.dp)).padding(12.dp)){Text("Потік роботи Skeleton",fontSize=15.sp,fontWeight=FontWeight.Bold,color=Text);Text("Де знаходиться робота і де вона застрягає. Кожна крапка — конкретна підтверджена задача.",fontSize=10.sp,lineHeight=14.sp,color=Muted,modifier=Modifier.padding(top=3.dp,bottom=10.dp));Row(Modifier.fillMaxWidth().height(190.dp),horizontalArrangement=Arrangement.spacedBy(3.dp)){stages.forEach{stage->val items=flow.items.filter{it.stage==stage};val blocked=items.count{it.blocked};val chosen=selected==stage;Column(Modifier.weight(1f).fillMaxHeight().clip(RoundedCornerShape(9.dp)).background(if(chosen)Color(0xFF252A34)else Color(0xFF111319)).clickable{selected=if(chosen)"" else stage}.padding(horizontal=3.dp,vertical=7.dp),horizontalAlignment=Alignment.CenterHorizontally){Text(items.size.toString(),fontSize=16.sp,fontWeight=FontWeight.Bold,color=if(blocked>0)Color(0xFFE3A65A)else if(items.isNotEmpty())Green else Muted);Box(Modifier.weight(1f),contentAlignment=Alignment.BottomCenter){Column(verticalArrangement=Arrangement.spacedBy(3.dp),horizontalAlignment=Alignment.CenterHorizontally){items.take(7).forEach{w->Box(Modifier.size(if(w.blocked)11.dp else 8.dp).clip(CircleShape).background(if(w.blocked)Color(0xFFE3A65A)else Color(0xFF8B78E6)))};if(items.size>7)Text("+${items.size-7}",fontSize=7.sp,color=Muted)}};Text(stage,fontSize=7.sp,lineHeight=8.sp,maxLines=2,textAlign=TextAlign.Center,color=if(chosen)Text else Muted,modifier=Modifier.height(20.dp))}}};if(flow.items.isEmpty())Text("Немає підтверджених задач для потоку.",fontSize=10.sp,color=Muted,modifier=Modifier.padding(top=8.dp))else Column(Modifier.padding(top=8.dp)){Text(if(selected.isBlank())"У потоці: ${flow.items.size}" else "$selected: ${active.size}",fontSize=10.sp,fontWeight=FontWeight.Bold,color=Text);active.take(4).forEach{w->Row(Modifier.fillMaxWidth().padding(top=5.dp),verticalAlignment=Alignment.Top){Box(Modifier.padding(top=4.dp).size(7.dp).clip(CircleShape).background(if(w.blocked)Color(0xFFE3A65A)else Color(0xFF8B78E6)));Text(w.title,fontSize=9.sp,lineHeight=12.sp,maxLines=2,overflow=TextOverflow.Ellipsis,color=if(w.blocked)Color(0xFFE3C08B)else Muted,modifier=Modifier.padding(start=6.dp).weight(1f))}}}}
}

@Composable private fun SkeletonScreen(api:HomeApi,active:Boolean,hyperion:Boolean,onHyperion:(Boolean)->Unit,outputTarget:String,onOutputTarget:(String)->Unit,onMenu:()->Unit){
    var live by remember{mutableStateOf(SkLiveData("attention","Читаю стан Skeleton…","Дані оновлюються автоматично.",emptyList(),"",emptyList(),emptyList(),emptyList(),emptyList(),listOf("state","timeline","heatmap"),0L))}
    var selectedView by rememberSaveable{mutableStateOf("state")}
    var loaded by remember{mutableStateOf(false)}
    LaunchedEffect(api.server,active){if(api.server!=null){suspend fun pull():Boolean{var ok=false;runCatching{api.get("/api/native/skeleton-dashboard")}.onSuccess{live=parseSkLive(it);loaded=true;ok=true};return ok};while(!loaded){if(pull())break;delay(1500)};if(active)while(true){delay(15000);pull()}}}
    Column(Modifier.fillMaxSize().background(Bg)){
        Header(ui("СК"),api.server!=null,hyperion,onHyperion,outputTarget,onOutputTarget,onMenu,subtitle="Skeleton")
        LazyColumn(Modifier.weight(1f).padding(horizontal=13.dp),contentPadding=PaddingValues(bottom=18.dp),verticalArrangement=Arrangement.spacedBy(10.dp)){
            item{Column(Modifier.fillMaxWidth().clip(RoundedCornerShape(16.dp)).background(Card).border(1.dp,Line,RoundedCornerShape(16.dp)).padding(16.dp)){Text(if(loaded)live.summaryTitle else "Читаю стан Skeleton…",fontSize=16.sp,fontWeight=FontWeight.SemiBold,color=Text);Text(if(loaded)"${live.summaryText} · Оновлено ${skAgeLabel(live.updatedAt)}" else "Дані з’являться автоматично.",fontSize=10.sp,lineHeight=15.sp,color=Muted,modifier=Modifier.padding(top=5.dp))}}
            if(selectedView=="state") item{SkWorkFlowViz(live.workFlow)}
            item{SkViewTabs(selectedView,{selectedView=it},live.views)}
            if(selectedView=="timeline"){
                item{SkTimelineCard(live.timeline)}
            }else if(selectedView=="heatmap"){
                item{SkHeatmap(live.heatmap)}
            }else{
                items(live.sections,key={it.id}){section->SkSectionCard(section)}
                if(live.nextMilestone.isNotBlank())item{SimpleSection("Наступний рубіж",live.nextMilestone)}
                if(live.recent.isNotEmpty())item{SimpleSection("Останні зміни",live.recent.joinToString("\n"){"• $it"})}
                if(live.domains.isNotEmpty())item{Text(ui("Домени"),fontSize=15.sp,fontWeight=FontWeight.Bold,color=Text,modifier=Modifier.padding(top=4.dp,bottom=2.dp))}
                items(live.domains,key={it.title}){domain->SkDomainCard(domain)}
            }
        }
    }
}




@Composable private fun SimpleSection(title:String,text:String){Column(Modifier.fillMaxWidth().clip(RoundedCornerShape(16.dp)).background(Card).border(1.dp,Line,RoundedCornerShape(16.dp)).padding(14.dp)){Text(title,fontSize=15.sp,fontWeight=FontWeight.Bold,color=Text);Text(text,fontSize=11.sp,lineHeight=16.sp,color=Muted,modifier=Modifier.padding(top=8.dp))}}






