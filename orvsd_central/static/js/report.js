$(function() {
    $.get("/1/report/stats", function(data) {
        $.each(data, function(k,v) {
            if (k === 'sites') {
                $("."+k).html(v);
            } else {
                $("#"+k).html(v);
            }
        });
    });
});
