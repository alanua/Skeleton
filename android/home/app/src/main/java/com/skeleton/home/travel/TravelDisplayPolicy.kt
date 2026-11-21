package com.skeleton.home.travel

/** Deliberately excludes S20 FE, S20+, Ultra, tablets and all TV devices. */
internal fun supportsTravelDisplay(manufacturer: String, model: String, television: Boolean): Boolean =
    !television && manufacturer.equals("samsung", ignoreCase = true) &&
        Regex("SM-G980[A-Z][A-Z0-9/]*|SM-G981[A-Z][A-Z0-9/]*").matches(model.uppercase(java.util.Locale.ROOT))
