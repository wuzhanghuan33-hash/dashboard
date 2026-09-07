// 注入保存的买家 cookie 到 9226 浏览器并导航到天猫详情页（验证登录态）。
// 用法: node buyer_inject_cookies.cjs [cookie_json_path] [port] [home_url]
const fs = require("fs");
const http = require("http");

const COOKIE_PATH = process.argv[2] || "/Users/luoxiaomin/.local/share/dashboard/buyer_cookies.json";
const PORT = process.argv[3] || 9226;
const HOME = process.argv[4] || "https://detail.tmall.com/item.htm?id=1029401782490&ns=1";

let all;
try {
  all = JSON.parse(fs.readFileSync(COOKIE_PATH, "utf8"));
} catch (e) {
  console.log("ERR_READ:" + e.message);
  process.exit(1);
}

http.get("http://localhost:" + PORT + "/json", (res) => {
  let d = "";
  res.on("data", (c) => (d += c));
  res.on("end", () => {
    let page;
    try {
      page = JSON.parse(d).find((t) => t.type === "page");
    } catch (e) {
      console.log("ERR_JSON");
      process.exit(1);
    }
    if (!page) {
      console.log("NO_PAGE");
      process.exit(1);
    }
    const sock = new WebSocket(page.webSocketDebuggerUrl);
    let id = 0;
    const send = (method, params) =>
      new Promise((resolve) => {
        const mid = ++id;
        const h = (e) => {
          const m = JSON.parse(e.data);
          if (m.id === mid) {
            sock.removeEventListener("message", h);
            resolve(m);
          }
        };
        sock.addEventListener("message", h);
        sock.send(JSON.stringify({ id: mid, method, params }));
      });
    sock.onopen = async () => {
      await send("Network.enable", {});
      const cookies = all.map((c) => ({
        name: c.name,
        value: c.value,
        domain: c.domain,
        path: c.path,
        expires: c.session ? -1 : c.expires,
        httpOnly: c.httpOnly,
        secure: c.secure,
        sameSite: c.sameSite || "None",
      }));
      await send("Network.setCookies", { cookies });
      await send("Page.navigate", { url: HOME });
      await new Promise((r2) => setTimeout(r2, 10000));
      const ex = await send("Runtime.evaluate", {
        expression: "location.href",
        returnByValue: true,
      });
      const href = ex.result && ex.result.result ? ex.result.result.value : "?";
      const loggedIn = href.indexOf("login") === -1;
      console.log("INJECTED:" + cookies.length + " HREF:" + href + " LOGIN:" + (loggedIn ? "OK" : "FAIL"));
      sock.close();
      process.exit(loggedIn ? 0 : 1);
    };
  });
});
