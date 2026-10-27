package com.skeleton.home

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class HistoryPickerBehaviorTest {
    @Test fun cachedHistoryOpensEvenIfRefreshIsBusy(){
        assertTrue(historyPickerCanOpen(true,true,true))
        assertTrue(historyPickerCanOpen(true,false,true))
    }
    @Test fun noCacheWaitingReadIsNotClickable(){
        assertFalse(historyPickerCanOpen(true,true,false))
        assertTrue(historyPickerCanOpen(true,false,false))
        assertFalse(historyPickerCanOpen(false,false,true))
    }
}
