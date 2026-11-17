/**
 * レシートプリンターへの印刷。
 *
 * iPad の Safari（Web アプリ）は Bluetooth の機器に直接つなげない。Bluetooth の接続はエプソンの無料アプリ
 * 「Epson TM Print Assistant」に任せ、NOXIA は伝票の中身（ePOS-Print XML。サーバーが作る）を URL でそのアプリに渡す。
 * プリンターの選択・ペアリングは iPad の設定とそのアプリで行う（NOXIA には出てこない）。
 *
 * URL の形は「Epson TM Print Assistant for iOS ユーザーズマニュアル Rev.D」3 章 URL Scheme のとおり:
 *   tmprintassistant://tmprintassistant.epson.com/print?ver=1&data-type=eposprintxml&data=<RFC3986 で URL エンコードした XML>
 *   - error-dialog=yes: エラー（紙切れ・カバー開き・つながらない等）はエプソンのアプリが画面に出す
 *   - timeout: Bluetooth の最初の接続に時間がかかることがあるので既定（15 秒）より長くする
 *   - success（印刷後に開く URL）は付けない。ホーム画面から開いた NOXIA には URL で戻れず、Safari が別に開いてしまうため。
 *     印刷のあとは画面左上の「◀ NOXIA」（Safari なら「◀ Safari」）で戻る。
 *   - cut は付けない（XML の最後で用紙を送って切っている）。paper-width・fit-to-width は PDF 用なので付けない。
 */

const TM_PRINT_ASSISTANT = "tmprintassistant://tmprintassistant.epson.com/print";
const TIMEOUT_MS = 30000;

/** RFC3986 の URL エンコード（encodeURIComponent が残す !'()* も変換する） */
function rfc3986(s: string): string {
  return encodeURIComponent(s).replace(/[!'()*]/g, (c) => `%${c.charCodeAt(0).toString(16).toUpperCase()}`);
}

/** TM Print Assistant に印刷を頼む URL */
export function tmPrintAssistantUrl(eposXml: string): string {
  return `${TM_PRINT_ASSISTANT}?ver=1&data-type=eposprintxml&error-dialog=yes&timeout=${TIMEOUT_MS}&data=${rfc3986(eposXml)}`;
}

/**
 * ほかのアプリ（エプソンの印刷アプリ・Square のアプリ）を URL で開く。
 * 開く前に「noxia:open-external」イベントで URL を知らせる（画面の通し確認で、渡した中身を確かめるため）。
 */
export function openExternalApp(url: string): void {
  window.dispatchEvent(new CustomEvent("noxia:open-external", { detail: url }));
  window.location.href = url;
}

/** エプソンのアプリを開いて印刷する（アプリが入っていなければ何も起きない） */
export function sendToPrinter(eposXml: string): void {
  openExternalApp(tmPrintAssistantUrl(eposXml));
}
