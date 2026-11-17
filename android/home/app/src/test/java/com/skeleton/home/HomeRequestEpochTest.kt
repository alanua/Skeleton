package com.skeleton.home

import org.junit.Assert.*
import org.junit.Test

class HomeRequestEpochTest {
    @Test fun oldObservationCannotOverwriteModeChange(){
        val gate=HomeRequestEpoch();val old=gate.snapshot();val next=gate.beginChange()
        assertFalse(gate.accepts(old));assertFalse(gate.accepts(next))
        gate.finishChange(next);assertTrue(gate.accepts(next));assertFalse(gate.accepts(old))
    }
    @Test fun olderCompletionCannotFinishNewerChange(){
        val gate=HomeRequestEpoch();val old=gate.beginChange();val next=gate.beginChange()
        gate.finishChange(old);assertFalse(gate.accepts(next))
        gate.finishChange(next);assertTrue(gate.accepts(next))
    }
}
