import * as cheerio from "cheerio";

// Cloudflare Worker that talks to mo2tmar-5edma.stmarkos.org on behalf of
// the Python script. Two endpoints, both require X-Worker-Secret to match
// the WORKER_SECRET binding (set via `wrangler secret put WORKER_SECRET`).
//
//   POST /check   { fogNumber: number }                  -> { available: boolean }
//   POST /register{ fogNumber: number, person: {...} }   -> { status, url, body }
//
// All other requests get 404.

const FOG_BASE = "https://mo2tmar-5edma.stmarkos.org";
const REG_PATH = "/fog_registration_form";
const CHECK_PATH = "/";

const FOG_MAP_NUMBER = {
  1: "الفوج الاول",
  2: "الفوج الثاني",
  3: "الفوج الثالث",
  4: "الفوج الرابع",
  5: "الفوج الخامس",
  6: "الفوج السادس",
  7: "الفوج السابع",
};

const BROWSER_HEADERS = {
  "User-Agent":
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 " +
    "(KHTML, like Gecko) Chrome/139.0.0.0 Safari/537.36",
  "Accept-Language": "ar,en;q=0.9",
};

function json(data, init = {}) {
  return new Response(JSON.stringify(data), {
    ...init,
    headers: { "Content-Type": "application/json", ...(init.headers || {}) },
  });
}

function unauthorized() {
  return json({ error: "unauthorized" }, { status: 401 });
}

function badRequest(reason) {
  return json({ error: reason }, { status: 400 });
}

async function authenticate(request, env) {
  const header = request.headers.get("X-Worker-Secret");
  if (!env.WORKER_SECRET || header !== env.WORKER_SECRET) {
    return false;
  }
  return true;
}

async function parseForm(html) {
  const $ = cheerio.load(html);
  const token = $('input[name="_token"]').attr("value") || null;
  const fromValues = $("#fromDate option")
    .map((_, el) => $(el).attr("value"))
    .get()
    .filter(Boolean);
  const toValues = $("#toDate option")
    .map((_, el) => $(el).attr("value"))
    .get()
    .filter(Boolean);
  return {
    token,
    fromDate: fromValues[0] || null,
    toDate: toValues[toValues.length - 1] || null,
  };
}

function buildRegistrationBody(fogNumber, fromDate, toDate, token, person) {
  return new URLSearchParams({
    _token: token,
    fogNumber: String(fogNumber),
    fromDate,
    toDate,
    transport: person.transport || "",
    name: person.name || "",
    nationalId: person.national_id || "",
    phone: person.phone || "",
    statues: person.statues || "",
    osra: person.osra || "",
    notes: person.notes || "",
    brotherAndSisterName: "",
    brotherAndSisterNationalId: "",
    brotherAndSisterPhone: "",
    brotherAndSisterNotes: "",
    engagedName: "",
    engagedNationalId: "",
    engagedPhone: "",
    engagedNotes: "",
    marriedName: "",
    marriedNationalId: "",
    marriedPhone: "",
    marriedNotes: "",
    childrenLessThan2Count: "0",
    childrenLessThan8Count: "0",
    childrenMoreThan8Count: "0",
    childrenAgesField: "",
    familyName: "",
    familyNationalId: "",
    familyPhone: "",
    familyNotes: "",
  });
}

async function checkAvailability(fogNumber) {
  const expected = FOG_MAP_NUMBER[fogNumber];
  if (!expected) {
    return { error: `unknown fogNumber ${fogNumber}` };
  }

  const response = await fetch(FOG_BASE + CHECK_PATH, {
    headers: BROWSER_HEADERS,
  });
  const html = await response.text();
  const $ = cheerio.load(html);

  let available = false;
  $("table tr").each((_, tr) => {
    if (available) return;
    const tds = $(tr).find("td");
    if (
      tds.length >= 2 &&
      $(tds[0]).text().trim() === expected &&
      $(tds[1]).text().trim() === "يوجد اماكن"
    ) {
      available = true;
    }
  });
  return { available };
}

async function registerPerson(fogNumber, person) {
  const regUrl = `${FOG_BASE}${REG_PATH}?TravellerType=servent&fogNumber=${fogNumber}`;
  const cookies = [];
  const setCookie = (resp) => {
    const sc = resp.headers.get("set-cookie");
    if (sc) cookies.push(sc.split(";")[0]);
  };

  const getResp = await fetch(regUrl, { headers: BROWSER_HEADERS });
  setCookie(getResp);
  const html = await getResp.text();
  const { token, fromDate, toDate } = await parseForm(html);

  if (!token || !fromDate || !toDate) {
    return {
      error: "failed to parse registration form",
      detail: { token, fromDate, toDate },
      status: getResp.status,
      url: regUrl,
    };
  }

  const body = buildRegistrationBody(fogNumber, fromDate, toDate, token, person);
  const postResp = await fetch(regUrl, {
    method: "POST",
    headers: {
      ...BROWSER_HEADERS,
      Referer: regUrl,
      "Content-Type": "application/x-www-form-urlencoded",
      Cookie: cookies.join("; "),
    },
    body,
  });
  const responseText = await postResp.text();

  return {
    status: postResp.status,
    url: postResp.url || regUrl,
    body: responseText.slice(0, 2000),
  };
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);

    if (request.method !== "POST") {
      return json({ error: "method not allowed" }, { status: 405 });
    }
    if (!(await authenticate(request, env))) {
      return unauthorized();
    }

    let payload;
    try {
      payload = await request.json();
    } catch {
      return badRequest("invalid JSON body");
    }

    try {
      if (url.pathname === "/check") {
        if (!Number.isInteger(payload.fogNumber)) {
          return badRequest("fogNumber must be an integer");
        }
        return json(await checkAvailability(payload.fogNumber));
      }

      if (url.pathname === "/register") {
        if (!Number.isInteger(payload.fogNumber) || !payload.person) {
          return badRequest("fogNumber and person are required");
        }
        return json(await registerPerson(payload.fogNumber, payload.person));
      }

      return json({ error: "not found" }, { status: 404 });
    } catch (err) {
      return json({ error: String(err && err.message ? err.message : err) }, { status: 500 });
    }
  },
};
