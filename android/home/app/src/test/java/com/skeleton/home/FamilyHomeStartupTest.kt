package com.skeleton.home

import org.junit.Assert.assertEquals
import org.junit.Test

class FamilyHomeStartupTest {
    @Test fun familyForegroundAlwaysReturnsHome() {
        assertEquals(0,homeTabOnForeground(false,2)) // previously Scanner
        assertEquals(0,homeTabOnForeground(false,1)) // previously Video
        assertEquals(0,homeTabOnForeground(false,0))
    }
    @Test fun operatorForegroundRetainsWorkingTab() {
        assertEquals(2,homeTabOnForeground(true,2))
        assertEquals(3,homeTabOnForeground(true,3))
    }
    @Test fun unknownProfileDoesNotPrematurelyChangeTab() {
        assertEquals(2,homeTabOnForeground(null,2))
    }
}
