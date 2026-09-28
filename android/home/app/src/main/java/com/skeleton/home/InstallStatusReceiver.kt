package com.skeleton.home

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.pm.PackageInstaller
import android.os.Build
import android.widget.Toast

class InstallStatusReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        when(intent.getIntExtra(PackageInstaller.EXTRA_STATUS,PackageInstaller.STATUS_FAILURE)) {
            PackageInstaller.STATUS_PENDING_USER_ACTION -> {
                val confirm=if(Build.VERSION.SDK_INT>=Build.VERSION_CODES.TIRAMISU)
                    intent.getParcelableExtra(Intent.EXTRA_INTENT,Intent::class.java)
                else @Suppress("DEPRECATION") intent.getParcelableExtra<Intent>(Intent.EXTRA_INTENT)
                if(confirm!=null) context.startActivity(confirm.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
                else Toast.makeText(context,"Android не повернув вікно підтвердження оновлення",Toast.LENGTH_LONG).show()
            }
            PackageInstaller.STATUS_SUCCESS -> Toast.makeText(context,"Home оновлено",Toast.LENGTH_SHORT).show()
            else -> {
                val message=intent.getStringExtra(PackageInstaller.EXTRA_STATUS_MESSAGE)?:"Оновлення Home не встановлено"
                Toast.makeText(context,message,Toast.LENGTH_LONG).show()
            }
        }
    }
    companion object { const val ACTION_INSTALL_STATUS="com.skeleton.home.INSTALL_STATUS" }
}
