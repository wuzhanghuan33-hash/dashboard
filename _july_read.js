(function(){
  var gv = function(r, c) {
    try {
      var v = window.spread.getSheet(0).getValue(r, c);
      return (v === null || v === undefined) ? '' : v;
    } catch(e) { return ''; }
  };
  var out = {};
  var rowIdx = {"weekday": 0, "date": 2, "gmv_target": 5, "gmv_actual": 6, "refund_amt": 10, "post_refund": 12, "visitors": 21, "buyers": 23, "aov": 27, "ref_rate": 16, "yoy": 8, "cart_users": 29, "cart_rate": 31, "cart_conv": 33, "visitors_target": 20, "buyers_target": 22, "aov_target": 26, "ref_rate_target": 15, "refund_amt_target": 9, "post_refund_target": 11, "conv_rate_target": 24, "cart_users_target": 28, "cart_rate_target": 30, "cart_conv_target": 32};
  var lyIdx = {"gmv_actual": 39};
  for (var k in rowIdx) {
    out[k] = [];
    for (var c = 4; c <= 34; c++) {
      out[k].push(gv(rowIdx[k], c));
    }
  }
  for (var lk in lyIdx) {
    out[lk] = [];
    for (var c = 4; c <= 34; c++) {
      out[lk].push(gv(lyIdx[lk], c));
    }
  }
  return JSON.stringify(out);
})()