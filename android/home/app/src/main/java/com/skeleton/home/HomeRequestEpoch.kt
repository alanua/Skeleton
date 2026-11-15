package com.skeleton.home

/** Invalidates observations started before a user mode change. UI-thread confined. */
internal class HomeRequestEpoch {
    private var generation = 0L
    private var changing = false
    fun snapshot():Long = generation
    fun beginChange():Long { changing=true;generation++;return generation }
    fun finishChange(token:Long) { if(token==generation)changing=false }
    fun accepts(token:Long):Boolean = token==generation&&!changing
}
