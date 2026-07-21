(function(){
  "use strict";

  /* ---------- ข้อความ / รูปแบบวันที่ ---------- */

  function esc(s){return String(s==null?"":s).replace(/[&<>"]/g,function(c){return{"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c];});}

  function bangkokStamp(){
    var parts={};
    new Intl.DateTimeFormat("en-CA",{timeZone:"Asia/Bangkok",year:"numeric",month:"2-digit",day:"2-digit",hour:"2-digit",minute:"2-digit",second:"2-digit",hourCycle:"h23"})
      .formatToParts(new Date()).forEach(function(p){parts[p.type]=p.value;});
    return parts.year+"-"+parts.month+"-"+parts.day+"-"+parts.hour+"-"+parts.minute+"-"+parts.second;
  }


  function formatDate(iso){
    if(!iso)return"—";
    var normalized=(/[Z+]/.test(iso.slice(10)))?iso:iso+"Z";
    var d=new Date(normalized);
    if(isNaN(d))return iso;
    return d.toLocaleString("en-GB",{timeZone:"Asia/Bangkok",day:"2-digit",month:"short",year:"2-digit",hour:"2-digit",minute:"2-digit"});
  }

  function splitTime(str){
    if(!str||str==="N/A")return["—","—"];
    var parts=str.split(/\s*[-–]\s*(?=\d{2}:\d{2})/);
    if(parts.length===2)return[parts[0].trim(),parts[1].trim()];
    var mid=str.indexOf(" - ");
    if(mid!==-1)return[str.slice(0,mid).trim(),str.slice(mid+3).trim()];
    return[str,"—"];
  }

  function formatTHT(str){
    if(!str||str==="—")return"—";
    return str.replace(/\s*THT\s*/gi," ").replace(/(\d{2}\/\d{2}\/)20(\d{2})/g,"$1$2").trim();
  }

  function formatDowntime(str){
    if(!str||str==="N/A"||str==="—")return"—";
    var m=str.match(/(\d+):(\d+)/);
    if(m)return parseInt(m[1])+" hr(s)";
    return str;
  }

  function extractLocation(name){
    if(!name)return"—";
    var idx=name.indexOf(" - ");
    if(idx===-1)return name.trim();
    var after=name.slice(idx+3).trim();
    var paren=after.lastIndexOf(")");
    if(paren!==-1)return after.slice(0,paren+1).trim();
    return after.replace(/\s+(Main|Back\s*up|Backup)$/i,"").trim();
  }

  function mapPriority(p){
    var m={"Critical":"Critical","Urgent":"Critical","High":"High","Medium":"Medium","Low":"Low","Scheduled":"Scheduled"};
    return m[p]||"Medium";
  }

  // Helpdesk note text often carries raw HTML (<br>, <b>, <a>) from WebHelpDesk.
  // Keep <br> as a line break for readability, strip every other tag rather than
  // rendering it — this is unsanitized third-party text, not markup we trust.
  function cleanNote(s){
    if(s==null)return"";
    return String(s)
      .replace(/<br\s*\/?>/gi,"\n")
      .replace(/<[^>]+>/g,"")
      .replace(/&nbsp;/gi," ")
      .replace(/[ \t]+\n/g,"\n")
      .trim();
  }

  /* ---------- export เป็น CSV / ไฟล์ดาวน์โหลด ---------- */

  function csvEscape(v){
    var s=String(v==null?"":v);
    if(/[",\n]/.test(s))return '"'+s.replace(/"/g,'""')+'"';
    return s;
  }

  function downloadBlob(content,filename,mime){
    var blob=new Blob([content],{type:mime});
    var url=URL.createObjectURL(blob);
    var a=document.createElement("a");
    a.href=url;a.download=filename;
    document.body.appendChild(a);a.click();document.body.removeChild(a);
    setTimeout(function(){URL.revokeObjectURL(url);},1000);
  }

  function setupExportMenu(){
    window.toggleExportMenu = function(){
      document.getElementById("exportMenu").classList.toggle("open");
    };
    document.addEventListener("click",function(e){
      var wrap=document.querySelector(".export-wrap");
      if(wrap&&!wrap.contains(e.target))document.getElementById("exportMenu").classList.remove("open");
    });
  }

  /* ---------- ธีมกะกลางวัน/กลางคืน ---------- */

  function defaultShiftByTime(){
    var hour=parseInt(new Date().toLocaleString("en-US",{timeZone:"Asia/Bangkok",hour:"numeric",hourCycle:"h23"}),10);
    return (hour>=20||hour<8)?"night":"day";
  }

  function applyTheme(s){
    document.documentElement.classList.toggle("light", s==="day");
    localStorage.setItem("nocTheme", s==="day"?"light":"dark");
  }


  function initShiftToggle(){
    var shift = window.__shift;
    function renderShiftLabel(){
      document.getElementById("toggle").dataset.shift=shift;
      document.getElementById("shiftIc").textContent=shift==="day"?"☀️":"🌙";
      document.getElementById("shiftTxt").textContent=shift==="day"?"กะกลางวัน":"กะกลางคืน";
    }
    renderShiftLabel();
    document.getElementById("toggle").addEventListener("click",function(){
      shift = shift==="day"?"night":"day";
      applyTheme(shift);
      renderShiftLabel();
    });
  }

  window.NOC = {
    esc: esc,
    bangkokStamp: bangkokStamp,
    formatDate: formatDate,
    splitTime: splitTime,
    formatTHT: formatTHT,
    formatDowntime: formatDowntime,
    extractLocation: extractLocation,
    mapPriority: mapPriority,
    cleanNote: cleanNote,
    csvEscape: csvEscape,
    downloadBlob: downloadBlob,
    setupExportMenu: setupExportMenu,
    defaultShiftByTime: defaultShiftByTime,
    applyTheme: applyTheme,
    initShiftToggle: initShiftToggle
  };


  if (document.currentScript && document.currentScript.hasAttribute("data-init-theme")) {
    var stored = localStorage.getItem("nocTheme");
    var shift = stored ? (stored === "light" ? "day" : "night") : defaultShiftByTime();
    document.documentElement.classList.toggle("light", shift === "day");
    window.__shift = shift;
  }
})();
