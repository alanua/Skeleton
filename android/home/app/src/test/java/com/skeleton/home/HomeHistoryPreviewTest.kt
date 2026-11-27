package com.skeleton.home
import org.junit.Test
import org.junit.Assert.*
class HomeHistoryPreviewTest{
    @Test fun partialResultsAreNeverMarkedComplete(){
        assertTrue(homeHistoryResultLabel(0,false,true,"").contains("неповний"))
        assertTrue(homeHistoryResultLabel(0,false,false,"failure").contains("недоступний"))
        assertTrue(homeHistoryResultLabel(10,true,false,"failure").contains("Кеш"))
        assertTrue(homeHistoryResultLabel(10,true,false,"").contains("Загалом"))
    }
}
