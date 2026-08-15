package com.skeleton.home.update

import android.app.Activity
import android.app.DownloadManager
import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.pm.PackageInstaller
import android.net.Uri
import android.os.Build
import android.provider.Settings
import android.widget.Toast
import com.skeleton.home.BuildConfig
import java.net.HttpURLConnection
import java.net.URL
import java.security.MessageDigest
import java.util.Locale
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.withContext
import org.json.JSONObject

data class HomeUpdateInfo(
    val versionCode: Int,
    val versionName: String,
    val sha256: String,
    val bytes: Long,
    val apkPath: String,
    internal val baseUrl: String,
)

class HomeUpdateManager(
    private val activity: Activity,
    configuredBaseUrls: String = BuildConfig.HOME_EDGE_BASE_URLS,
) {
    private val baseUrls = HomeUpdatePolicy.configuredBaseUrls(configuredBaseUrls)
    private var pendingInstallUri: Uri? = null

    val canCheckForUpdates: Boolean
        get() = activity.packageName == HomeUpdatePolicy.ProductionPackage && baseUrls.isNotEmpty()

    fun installedVersionCode(): Int {
        val info = activity.packageManager.getPackageInfo(activity.packageName, 0)
        return if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.P) {
            info.longVersionCode.toInt()
        } else {
            @Suppress("DEPRECATION")
            info.versionCode
        }
    }

    suspend fun latestAvailable(): HomeUpdateInfo? = withContext(Dispatchers.IO) {
        if (!canCheckForUpdates) return@withContext null
        val installed = installedVersionCode()
        for (baseUrl in baseUrls) {
            val candidate = runCatching { fetch(baseUrl) }.getOrNull() ?: continue
            if (HomeUpdatePolicy.isNewer(installed, candidate.versionCode)) return@withContext candidate
        }
        null
    }

    private fun fetch(baseUrl: String): HomeUpdateInfo {
        val connection = URL(baseUrl + HomeUpdatePolicy.UpdatePath).openConnection() as HttpURLConnection
        connection.requestMethod = "GET"
        connection.connectTimeout = 2_000
        connection.readTimeout = 5_000
        connection.setRequestProperty("Accept", "application/json")
        return try {
            val code = connection.responseCode
            val stream = if (code in 200..299) connection.inputStream else connection.errorStream
            val body = stream?.bufferedReader(Charsets.UTF_8)?.use { it.readText() }.orEmpty()
            if (code !in 200..299) throw IllegalStateException("Home update endpoint HTTP $code")
            val value = JSONObject(body)
            if (value.optString("schema") != HomeUpdatePolicy.UpdateSchema) throw IllegalStateException("Unexpected Home update schema")
            val versionCode = value.optInt("version_code", 0)
            val versionName = value.optString("version_name").trim()
            val sha256 = value.optString("sha256").trim().lowercase(Locale.ROOT)
            val bytes = value.optLong("bytes", 0L)
            val apkPath = value.optString("apk_path").trim()
            if (versionCode <= 0 || versionName.isBlank() || bytes <= 0L || !HomeUpdatePolicy.validSha256(sha256) || !HomeUpdatePolicy.validApkPath(apkPath)) {
                throw IllegalStateException("Invalid Home update manifest")
            }
            HomeUpdateInfo(versionCode, versionName, sha256, bytes, apkPath, baseUrl)
        } finally {
            connection.disconnect()
        }
    }

    suspend fun downloadAndRequestInstall(
        info: HomeUpdateInfo,
        onProgress: (String) -> Unit = {},
    ) {
        if (!canCheckForUpdates) throw IllegalStateException("Production update channel is not configured")
        if (!HomeUpdatePolicy.isNewer(installedVersionCode(), info.versionCode)) return
        val uri = downloadVerified(info, onProgress)
        withContext(Dispatchers.Main) { requestInstall(uri) }
    }

    private suspend fun downloadVerified(info: HomeUpdateInfo, onProgress: (String) -> Unit): Uri =
        withContext(Dispatchers.IO) {
            val manager = activity.getSystemService(Context.DOWNLOAD_SERVICE) as DownloadManager
            val request = DownloadManager.Request(Uri.parse(info.baseUrl + info.apkPath))
                .setTitle("Home ${info.versionName}")
                .setDescription("Оновлення Skeleton Home")
                .setMimeType("application/vnd.android.package-archive")
                .setNotificationVisibility(DownloadManager.Request.VISIBILITY_VISIBLE_NOTIFY_COMPLETED)
            val downloadId = manager.enqueue(request)
            var result: Uri? = null
            while (result == null) {
                manager.query(DownloadManager.Query().setFilterById(downloadId)).use { cursor ->
                    if (!cursor.moveToFirst()) throw IllegalStateException("Android не знайшов завантаження")
                    val status = cursor.getInt(cursor.getColumnIndexOrThrow(DownloadManager.COLUMN_STATUS))
                    val total = cursor.getLong(cursor.getColumnIndexOrThrow(DownloadManager.COLUMN_TOTAL_SIZE_BYTES))
                    val done = cursor.getLong(cursor.getColumnIndexOrThrow(DownloadManager.COLUMN_BYTES_DOWNLOADED_SO_FAR))
                    if (total > 0L && done >= 0L) {
                        val percent = ((done * 100L) / total).coerceIn(0L, 100L)
                        withContext(Dispatchers.Main) { onProgress("Завантажую… $percent%") }
                    }
                    when (status) {
                        DownloadManager.STATUS_SUCCESSFUL -> {
                            val uri = manager.getUriForDownloadedFile(downloadId)
                                ?: throw IllegalStateException("Android не повернув APK")
                            val digest = MessageDigest.getInstance("SHA-256")
                            var actualBytes = 0L
                            activity.contentResolver.openInputStream(uri)?.use { input ->
                                val buffer = ByteArray(64 * 1024)
                                while (true) {
                                    val count = input.read(buffer)
                                    if (count <= 0) break
                                    actualBytes += count
                                    digest.update(buffer, 0, count)
                                }
                            } ?: throw IllegalStateException("Не вдалося прочитати APK")
                            val actualSha = digest.digest().joinToString("") { "%02x".format(it.toInt() and 0xff) }
                            if (actualSha != info.sha256 || actualBytes != info.bytes) {
                                manager.remove(downloadId)
                                throw IllegalStateException("Перевірка оновлення не пройдена")
                            }
                            result = uri
                        }
                        DownloadManager.STATUS_FAILED -> {
                            val reason = cursor.getInt(cursor.getColumnIndexOrThrow(DownloadManager.COLUMN_REASON))
                            manager.remove(downloadId)
                            throw IllegalStateException("Завантаження не вдалося ($reason)")
                        }
                    }
                }
                if (result == null) delay(350)
            }
            result!!
        }

    private fun requestInstall(uri: Uri) {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O && !activity.packageManager.canRequestPackageInstalls()) {
            pendingInstallUri = uri
            activity.startActivity(
                Intent(Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES, Uri.parse("package:${activity.packageName}")),
            )
            return
        }
        installWithPackageInstaller(uri)
    }

    fun onResume() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O && activity.packageManager.canRequestPackageInstalls()) {
            pendingInstallUri?.let { uri ->
                pendingInstallUri = null
                installWithPackageInstaller(uri)
            }
        }
    }

    private fun installWithPackageInstaller(uri: Uri) {
        check(activity.packageName == HomeUpdatePolicy.ProductionPackage) { "Preview build cannot install production updates" }
        val installer = activity.packageManager.packageInstaller
        val params = PackageInstaller.SessionParams(PackageInstaller.SessionParams.MODE_FULL_INSTALL).apply {
            setAppPackageName(HomeUpdatePolicy.ProductionPackage)
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
                setRequireUserAction(PackageInstaller.SessionParams.USER_ACTION_REQUIRED)
            }
        }
        val sessionId = installer.createSession(params)
        installer.openSession(sessionId).use { session ->
            activity.contentResolver.openInputStream(uri)?.use { input ->
                session.openWrite("Home-update.apk", 0, -1).use { output ->
                    input.copyTo(output)
                    session.fsync(output)
                }
            } ?: throw IllegalStateException("Не вдалося відкрити завантажений APK")
            val resultIntent = Intent(activity, HomeUpdateInstallStatusReceiver::class.java)
                .setAction(HomeUpdateInstallStatusReceiver.ActionInstallStatus)
            val flags = PendingIntent.FLAG_UPDATE_CURRENT or
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) PendingIntent.FLAG_MUTABLE else 0
            val pending = PendingIntent.getBroadcast(activity, sessionId, resultIntent, flags)
            session.commit(pending.intentSender)
        }
    }
}

class HomeUpdateInstallStatusReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        when (intent.getIntExtra(PackageInstaller.EXTRA_STATUS, PackageInstaller.STATUS_FAILURE)) {
            PackageInstaller.STATUS_PENDING_USER_ACTION -> {
                @Suppress("DEPRECATION")
                val confirmation = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
                    intent.getParcelableExtra(Intent.EXTRA_INTENT, Intent::class.java)
                } else {
                    intent.getParcelableExtra(Intent.EXTRA_INTENT)
                }
                confirmation?.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                if (confirmation != null) context.startActivity(confirmation)
            }
            PackageInstaller.STATUS_SUCCESS ->
                Toast.makeText(context, "Home оновлено", Toast.LENGTH_SHORT).show()
            else -> {
                val message = intent.getStringExtra(PackageInstaller.EXTRA_STATUS_MESSAGE) ?: "Оновлення не встановлено"
                Toast.makeText(context, message, Toast.LENGTH_LONG).show()
            }
        }
    }

    companion object {
        const val ActionInstallStatus = "com.skeleton.home.UPDATE_INSTALL_STATUS"
    }
}
