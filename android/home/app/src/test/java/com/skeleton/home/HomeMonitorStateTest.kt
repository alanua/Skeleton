package com.skeleton.home

import org.junit.Assert.*
import org.junit.Test

class HomeMonitorStateTest {
    private val a=HomeMonitorIdentity("synthetic-server", "synthetic-endpoint", "SYNTHETIC-A")
    private val b=a.copy(job="SYNTHETIC-B")

    @Test fun currentJobRejectsOldReadEvenBeforeAnotherRequest(){
        val gate=HomeMonitorRequests();val token=gate.read(a)!!
        assertFalse(gate.accepts(token,b))
        assertTrue(gate.accepts(token,a))
    }
    @Test fun serverAndEndpointChangesRejectOldReadAndWrite(){
        for(write in listOf(false,true)){
            val gate=HomeMonitorRequests();val token=if(write)gate.write(a) else gate.read(a)!!
            assertFalse(gate.accepts(token,a.copy(server="other-server")))
            assertFalse(gate.accepts(token,a.copy(endpoint="other-endpoint")))
            assertFalse(gate.accepts(token,b))
        }
    }
    @Test fun readPrecedingWriteCannotUndoWrite(){
        val gate=HomeMonitorRequests();val read=gate.read(a)!!;val write=gate.write(a)
        assertFalse(gate.accepts(read,a));assertTrue(gate.accepts(write,a))
        assertNull(gate.read(a))
        gate.finish(write,a);assertNotNull(gate.read(a));assertFalse(gate.accepts(read,a))
    }
    @Test fun oldWriteCompletionCannotFinishCurrentWrite(){
        val gate=HomeMonitorRequests();val old=gate.write(a);val current=gate.write(b)
        gate.finish(old,b);assertNull(gate.read(b));assertFalse(gate.accepts(old,b))
        gate.finish(current,b);assertNotNull(gate.read(b))
    }
    @Test fun returningToAStillRejectsEarlierAResult(){
        val gate=HomeMonitorRequests();val old=gate.read(a)!!
        gate.invalidate();gate.read(b);gate.invalidate();gate.read(a)
        assertFalse(gate.accepts(old,a))
    }
    @Test fun staleErrorsUseTheSameAcceptanceGate(){
        val gate=HomeMonitorRequests();val error=gate.write(a);gate.invalidate();gate.read(b)
        assertFalse(gate.accepts(error,b));gate.finish(error,b)
        assertNotNull(gate.read(b))
    }
    @Test fun serverCapabilityOverridesLegacyTmdbFallback(){
        assertFalse(monitorEligible(true,true,false,901))
        assertTrue(monitorEligible(true,true,true,0))
        assertTrue(monitorEligible(true,true,null,901))
        assertFalse(monitorEligible(true,true,null,0))
    }
    @Test fun genuineTvAndJobAreRequired(){
        assertFalse(monitorEligible(false,true,true,901))
        assertFalse(monitorEligible(true,false,true,901))
        assertFalse(monitorEligible(true,false,null,901))
    }
    @Test fun unsupportedTimeoutAndErrorAreDistinct(){
        assertEquals("UNSUPPORTED",monitorFailure(409,false))
        assertEquals("TIMEOUT",monitorFailure(504,false))
        assertEquals("TIMEOUT",monitorFailure(null,true))
        assertEquals("ERROR",monitorFailure(500,false))
        assertEquals("ERROR",monitorFailure(null,false))
    }
}
