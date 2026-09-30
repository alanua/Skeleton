package com.skeleton.home

import android.app.Activity
import android.graphics.Color
import android.os.Bundle
import android.view.Gravity
import android.view.ViewGroup
import android.widget.Button
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView

class HealthPermissionsRationaleActivity : Activity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        val density = resources.displayMetrics.density
        fun dp(value: Int) = (value * density).toInt()

        val content = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(24), dp(28), dp(24), dp(28))
            setBackgroundColor(Color.rgb(10, 14, 18))
        }

        content.addView(TextView(this).apply {
            text = "Доступ до даних здоров’я"
            textSize = 24f
            setTextColor(Color.WHITE)
            setPadding(0, 0, 0, dp(18))
        }, LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT))

        content.addView(TextView(this).apply {
            text = "Home запитує лише читання кроків та сесій/стадій сну з Health Connect. Дані використовуються для локального Skeleton-контуру телефону. Сирі сенсорні потоки, геолокація, повідомлення та інші медичні категорії не читаються."
            textSize = 16f
            setTextColor(Color.rgb(210, 214, 220))
            setLineSpacing(0f, 1.2f)
        }, LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT))

        content.addView(TextView(this).apply {
            text = "Доступ можна будь-коли відкликати у Health Connect."
            textSize = 14f
            setTextColor(Color.rgb(150, 158, 168))
            setPadding(0, dp(18), 0, dp(24))
        }, LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT))

        content.addView(Button(this).apply {
            text = "Закрити"
            setOnClickListener { finish() }
        }, LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, dp(52)).apply {
            gravity = Gravity.CENTER_HORIZONTAL
        })

        setContentView(ScrollView(this).apply { addView(content) })
    }
}
