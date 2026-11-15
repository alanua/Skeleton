package com.skeleton.home

import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test

class HistoryVisibilityRegressionTest{
    private fun item(id:String,title:String,hidden:Boolean=false)=JSONObject().put("history_key",id).put("title",title).put("hidden",hidden)
    @Test fun normalRowsAndCounterBothExcludeHidden(){
        val result=homeHistoryPickerResult(listOf(item("a","Visible"),item("b","Hidden",true)),"")
        assertEquals(1,result.total);assertEquals(listOf("a"),result.items.map{it.getString("history_key")})
    }
    @Test fun searchDenominatorIsVisibleCatalogAndNeverRevealsHidden(){
        val result=homeHistoryPickerResult(listOf(item("a","Alpha"),item("b","Beta"),item("c","Alpha hidden",true)),"alpha")
        assertEquals(2,result.total);assertEquals(1,result.items.size)
    }
    @Test fun duplicatesDoNotInflateNormalCounter(){
        val result=homeHistoryPickerResult(listOf(item("a","Alpha"),item("a","Alpha again")),"")
        assertEquals(1,result.total);assertEquals(1,result.items.size)
    }
    @Test fun editorSourceIsUnmodifiedAndCanRestoreAHiddenWork(){
        val hidden=item("a","Alpha",true);val input=listOf(hidden)
        assertEquals(0,homeHistoryPickerResult(input,"").total)
        assertTrue(input.single().getBoolean("hidden"))
        hidden.put("hidden",false)
        assertEquals(1,homeHistoryPickerResult(input,"").total)
    }
    @Test fun currentSnapshotChangesMembershipAfterEdit(){
        val before=listOf(item("a","Alpha"),item("b","Beta",true))
        val after=listOf(item("a","Alpha",true),item("b","Beta"),item("c","New"))
        assertEquals(listOf("a"),homeHistoryPickerResult(before,"").items.map{it.getString("history_key")})
        assertEquals(listOf("b","c"),homeHistoryPickerResult(after,"").items.map{it.getString("history_key")})
    }
}
