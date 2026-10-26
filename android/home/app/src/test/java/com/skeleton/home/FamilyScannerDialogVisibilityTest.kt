package com.skeleton.home

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class FamilyScannerDialogVisibilityTest {
    @Test fun inactiveScannerNeverOverlaysHome() {
        assertFalse(familyScannerDialogVisible(false,true,true))
        assertFalse(familyScannerDialogVisible(false,false,true))
    }
    @Test fun scannerNeedsConnectionAndExplicitOpenState() {
        assertFalse(familyScannerDialogVisible(true,true,false))
        assertFalse(familyScannerDialogVisible(true,false,true))
        assertTrue(familyScannerDialogVisible(true,true,true))
    }
}
