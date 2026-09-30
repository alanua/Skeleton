package com.skeleton.home

import android.Manifest
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.location.Location
import android.location.LocationListener
import android.location.LocationManager
import android.os.Build
import android.os.Handler
import android.os.IBinder
import android.os.Looper
import androidx.core.app.NotificationCompat
import androidx.core.content.ContextCompat
import org.json.JSONObject
import java.io.File
import java.net.HttpURLConnection
import java.net.URL
import java.time.Instant
import java.util.concurrent.Executors

class LocationTrackingService : Service(), LocationListener {
    companion object {
        private const val CHANNEL_ID = "skeleton_geo_tracking"
        private const val NOTIFICATION_ID = 4316
        private const val PREFS = "skeleton_geo_tracking"
        private const val KEY_ENABLED = "enabled"
        private const val KEY_LAST_OBSERVED_AT = "last_observed_at"
        private const val KEY_LAST_UPLOAD_AT = "last_upload_at"
        private const val KEY_BUFFERED = "buffered"
        private const val ACTION_START = "com.skeleton.home.geo.START"
        private const val MIN_TIME_MS = 5 * 60 * 1000L
        private const val MIN_DISTANCE_M = 100f
        private const val HEARTBEAT_MS = 15 * 60 * 1000L
        private const val MAX_BUFFER_LINES = 2000

        fun isTrackingEnabled(context: Context): Boolean =
            context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).getBoolean(KEY_ENABLED, false)

        fun bufferedCount(context: Context): Int =
            context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).getInt(KEY_BUFFERED, 0)

        fun lastObservedAt(context: Context): String =
            context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).getString(KEY_LAST_OBSERVED_AT, "").orEmpty()

        fun hasBackgroundLocation(context: Context): Boolean =
            Build.VERSION.SDK_INT < Build.VERSION_CODES.Q ||
                ContextCompat.checkSelfPermission(context, Manifest.permission.ACCESS_BACKGROUND_LOCATION) == PackageManager.PERMISSION_GRANTED

        fun markRestartNeeded(context: Context, needed: Boolean) {
            context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit().putBoolean("restart_needed", needed).apply()
        }

        fun isRestartNeeded(context: Context): Boolean =
            context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).getBoolean("restart_needed", false)

        fun start(context: Context) {
            context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit().putBoolean(KEY_ENABLED, true).apply()
            ContextCompat.startForegroundService(
                context,
                Intent(context, LocationTrackingService::class.java).setAction(ACTION_START),
            )
        }

        fun stop(context: Context) {
            context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit().putBoolean(KEY_ENABLED, false).apply()
            context.stopService(Intent(context, LocationTrackingService::class.java))
        }
    }

    private val io = Executors.newSingleThreadExecutor()
    private val heartbeatHandler = Handler(Looper.getMainLooper())
    private lateinit var locationManager: LocationManager
    private val bufferFile by lazy { File(filesDir, "geo-track-buffer.jsonl") }
    private val heartbeatRunnable = object : Runnable {
        override fun run() {
            if (isTrackingEnabled(this@LocationTrackingService)) {
                io.execute { sendHeartbeat() }
                heartbeatHandler.postDelayed(this, HEARTBEAT_MS)
            }
        }
    }

    override fun onCreate() {
        super.onCreate()
        locationManager = getSystemService(Context.LOCATION_SERVICE) as LocationManager
        createChannel()
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (!isTrackingEnabled(this)) {
            stopTracking()
            stopSelf()
            return START_NOT_STICKY
        }
        startForeground(NOTIFICATION_ID, notification())
        if (!hasLocationPermission()) {
            getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit().putBoolean(KEY_ENABLED, false).apply()
            stopTracking()
            stopSelf()
            return START_NOT_STICKY
        }
        requestUpdates()
        markRestartNeeded(this, false)
        heartbeatHandler.removeCallbacks(heartbeatRunnable)
        heartbeatHandler.post(heartbeatRunnable)
        io.execute { flushBuffer() }
        return START_STICKY
    }

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onDestroy() {
        heartbeatHandler.removeCallbacks(heartbeatRunnable)
        stopTracking()
        io.shutdown()
        super.onDestroy()
    }

    override fun onLocationChanged(location: Location) {
        if (!isTrackingEnabled(this)) return
        val observedAt = Instant.ofEpochMilli(location.time.coerceAtLeast(0L)).toString()
        getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit()
            .putString(KEY_LAST_OBSERVED_AT, observedAt)
            .apply()

        val point = JSONObject()
            .put("schema", "skeleton.geo.track_point.v1")
            .put("observed_at", observedAt)
            .put("latitude", location.latitude)
            .put("longitude", location.longitude)
            .put("provider", normalizeProvider(location.provider))
        if (location.hasAccuracy()) point.put("accuracy_m", location.accuracy.toDouble())
        if (location.hasAltitude()) point.put("altitude_m", location.altitude)
        if (location.hasSpeed()) point.put("speed_mps", location.speed.toDouble())
        if (location.hasBearing()) point.put("bearing_deg", location.bearing.toDouble())

        io.execute {
            appendBuffered(point.toString())
            flushBuffer()
        }
    }

    @Suppress("MissingPermission")
    private fun requestUpdates() {
        stopTracking()
        for (provider in listOf(LocationManager.NETWORK_PROVIDER, LocationManager.GPS_PROVIDER)) {
            runCatching {
                if (locationManager.isProviderEnabled(provider)) {
                    locationManager.requestLocationUpdates(
                        provider,
                        MIN_TIME_MS,
                        MIN_DISTANCE_M,
                        this,
                        Looper.getMainLooper(),
                    )
                }
            }
        }
    }

    private fun stopTracking() {
        runCatching { locationManager.removeUpdates(this) }
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.N) {
            runCatching { stopForeground(STOP_FOREGROUND_REMOVE) }
        } else {
            @Suppress("DEPRECATION")
            runCatching { stopForeground(true) }
        }
    }

    private fun hasLocationPermission(): Boolean =
        ContextCompat.checkSelfPermission(this, Manifest.permission.ACCESS_FINE_LOCATION) == PackageManager.PERMISSION_GRANTED ||
            ContextCompat.checkSelfPermission(this, Manifest.permission.ACCESS_COARSE_LOCATION) == PackageManager.PERMISSION_GRANTED

    private fun appendBuffered(line: String) {
        val lines = if (bufferFile.isFile) bufferFile.readLines().takeLast(MAX_BUFFER_LINES - 1) else emptyList()
        val tmp = File(bufferFile.parentFile, bufferFile.name + ".tmp")
        tmp.bufferedWriter().use { out ->
            lines.forEach { out.appendLine(it) }
            out.appendLine(line)
        }
        if (!tmp.renameTo(bufferFile)) {
            bufferFile.writeText(tmp.readText())
            tmp.delete()
        }
        updateBufferedCount()
    }

    private fun flushBuffer() {
        if (!bufferFile.isFile) {
            updateBufferedCount()
            return
        }
        val lines = bufferFile.readLines().filter { it.isNotBlank() }
        if (lines.isEmpty()) {
            bufferFile.delete()
            updateBufferedCount()
            return
        }

        var consumed = 0
        for (line in lines) {
            if (!send(line)) break
            consumed++
        }
        if (consumed > 0) {
            val remaining = lines.drop(consumed)
            if (remaining.isEmpty()) bufferFile.delete()
            else {
                val tmp = File(bufferFile.parentFile, bufferFile.name + ".tmp")
                tmp.writeText(remaining.joinToString(separator = "\n", postfix = "\n"))
                if (!tmp.renameTo(bufferFile)) {
                    bufferFile.writeText(tmp.readText())
                    tmp.delete()
                }
            }
        }
        updateBufferedCount()
    }

    private fun sendHeartbeat(): Boolean {
        val body = JSONObject()
            .put("schema", "skeleton.geo.track_heartbeat.v1")
            .put("observed_at", Instant.now().toString())
            .put("tracking_enabled", isTrackingEnabled(this))
            .toString()
        return postJson("/api/native/home-edge/geo/track/heartbeat", body, updateLastUpload = false)
    }

    private fun send(body: String): Boolean =
        postJson("/api/native/home-edge/geo/track", body, updateLastUpload = true)

    private fun postJson(path: String, body: String, updateLastUpload: Boolean): Boolean {
        for (base in BuildConfig.HOME_EDGE_BASE_URLS.split(',').map { it.trim().trimEnd('/') }.filter { it.startsWith("http://") || it.startsWith("https://") }.distinct()) {
            val ok = runCatching {
                val c = URL(base + path).openConnection() as HttpURLConnection
                c.connectTimeout = 3500
                c.readTimeout = 7000
                c.requestMethod = "POST"
                c.doOutput = true
                c.setRequestProperty("Accept", "application/json")
                c.setRequestProperty("Content-Type", "application/json")
                c.outputStream.use { it.write(body.toByteArray(Charsets.UTF_8)) }
                val code = c.responseCode
                runCatching { (if (code >= 400) c.errorStream else c.inputStream)?.close() }
                c.disconnect()
                code in 200..299
            }.getOrDefault(false)
            if (ok) {
                if (updateLastUpload) {
                    getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit()
                        .putString(KEY_LAST_UPLOAD_AT, Instant.now().toString())
                        .apply()
                }
                return true
            }
        }
        return false
    }

    private fun updateBufferedCount() {
        val count = if (bufferFile.isFile) bufferFile.useLines { it.count() } else 0
        getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit().putInt(KEY_BUFFERED, count).apply()
    }

    private fun normalizeProvider(provider: String?): String =
        when (provider?.lowercase()) {
            "gps" -> "gps"
            "network" -> "network"
            "passive" -> "passive"
            "fused" -> "fused"
            else -> "unknown"
        }

    private fun createChannel() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            val channel = NotificationChannel(
                CHANNEL_ID,
                "Skeleton location tracking",
                NotificationManager.IMPORTANCE_LOW,
            ).apply {
                description = "Активний тракінг місцезнаходження для Skeleton Geo"
                setShowBadge(false)
            }
            (getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager)
                .createNotificationChannel(channel)
        }
    }

    private fun notification(): android.app.Notification {
        val pending = PendingIntent.getActivity(
            this,
            0,
            Intent(this, MainActivity::class.java),
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )
        return NotificationCompat.Builder(this, CHANNEL_ID)
            .setSmallIcon(R.mipmap.ic_launcher)
            .setContentTitle("Skeleton · Тракінг активний")
            .setContentText("Місцезнаходження записується лише у приватний Geo-контур.")
            .setOngoing(true)
            .setContentIntent(pending)
            .setCategory(NotificationCompat.CATEGORY_SERVICE)
            .build()
    }
}
