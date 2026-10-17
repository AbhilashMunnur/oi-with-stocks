const app = document.querySelector("#app");

function inr(amount) {
  const value = Number(amount);
  if (!Number.isFinite(value)) return "n/a";
  const sign = value < 0 ? "-" : "";
  return sign + "₹" + Math.abs(value).toLocaleString("en-IN", {
    maximumFractionDigits: 0,
  });
}

function px(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) return "";
  return number.toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function moneyClass(amount) {
  if (amount > 0) return "money up";
  if (amount < 0) return "money down";
  return "money";
}

function side(direction) {
  return direction === "LONG" ? "Buy" : direction === "SHORT" ? "Sell" : direction || "";
}

function when(stamp) {
  return String(stamp || "").replace("T", " ").slice(0, 16);
}

function bookByName(desk, name) {
  return (desk.books || []).find((book) => book.name === name);
}

function landing(desk) {
  const tiles = (desk.books || []).map((book) => `
    <a class="tile" href="#${encodeURIComponent(book.name)}">
      <strong>${book.name}</strong>
      <div class="${moneyClass(book.realised)}">Realised ${inr(book.realised)}</div>
      <div class="${moneyClass(book.unrealised)}">Unrealised ${inr(book.unrealised)}</div>
      <span>${book.open_count || 0} open · margin ${inr(book.margin)}</span>
    </a>`).join("");
  return `
    <h1>Stock paper books</h1>
    <p class="lede">Four separate ₹4 crore books. Realised is profit already booked. Unrealised is the open lots at the last scan price. Updated ${desk.generated_at}.</p>
    <nav class="tiles">${tiles}</nav>
    <p class="foot">Public page. Paper trades only.</p>`;
}

function bookPage(book) {
  const open = (book.open || []).map((row) => `
    <tr>
      <td>${row.symbol}</td>
      <td>${side(row.direction)}</td>
      <td class="num">${row.lots_open}/${row.lots_total}</td>
      <td class="num">${px(row.entry_price)}</td>
      <td class="num">${px(row.mark)}</td>
      <td>${when(row.entry_time)}</td>
      <td>${row.expiry || ""}</td>
      <td class="num ${moneyClass(row.booked)}">${inr(row.booked)}</td>
    </tr>`).join("") || `<tr><td colspan="8">No open lots.</td></tr>`;
  const exits = (book.recent_exits || []).map((row) => `
    <tr>
      <td>${row.symbol}</td>
      <td>${row.side}</td>
      <td>${row.exit_date}</td>
      <td>${row.reason}</td>
      <td class="num ${moneyClass(row.pnl)}">${inr(row.pnl)}</td>
    </tr>`).join("") || `<tr><td colspan="5">No booked exits yet.</td></tr>`;
  return `
    <a class="back" href="#">&larr; All books</a>
    <h1>${book.name}</h1>
    <p class="${moneyClass(book.realised)} big">Realised ${inr(book.realised)}</p>
    <p class="${moneyClass(book.unrealised)} big">Unrealised ${inr(book.unrealised)}</p>
    <p class="meta">${book.closed_count || 0} closed trades · ${book.open_lots || 0} lots still open · margin ${inr(book.margin)} · saved ${book.updated_at || ""}</p>
    <h2>Open</h2>
    <table>
      <thead><tr><th>Symbol</th><th>Side</th><th class="num">Lots</th><th class="num">Entry</th><th class="num">Price</th><th>Entered</th><th>Expiry</th><th class="num">Booked</th></tr></thead>
      <tbody>${open}</tbody>
    </table>
    <h2>Latest booked exits</h2>
    <table>
      <thead><tr><th>Symbol</th><th>Side</th><th>Exit</th><th>Reason</th><th class="num">P&amp;L</th></tr></thead>
      <tbody>${exits}</tbody>
    </table>`;
}

function render(desk) {
  const name = decodeURIComponent(location.hash.slice(1));
  const book = name ? bookByName(desk, name) : null;
  app.innerHTML = book ? bookPage(book) : landing(desk);
  window.scrollTo(0, 0);
}

fetch("data/desk.json?v=" + Date.now(), { cache: "no-store" })
  .then((response) => {
    if (!response.ok) throw new Error("missing desk");
    return response.json();
  })
  .then((desk) => {
    render(desk);
    window.addEventListener("hashchange", () => render(desk));
  })
  .catch(() => {
    app.innerHTML = `<h1>Stock paper books</h1><p class="status">The book file is not on this page yet.</p>`;
  });
