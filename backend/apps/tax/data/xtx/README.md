# XTX（e-Tax 申告データ）対応表の置き場所

`apps/tax/exports/xtx.py` は、この下の `<年度>/fields.json` が無い年度では XTX を生成しません（推測で作らない）。

## 用意するもの（年度ごと）

1. 国税庁 e-Tax「仕様公開」から、その年度の受付開始分の **XML 構造設計書及び帳票フィールド仕様書【所得税関係】**、**帳票間連動仕様書**、**XML スキーマ** を取得する
   （令和9年1月4日受付開始分＝令和8年分: ドラフト 2026-10-30、確定版 2026-11-25 掲載予定。 https://www.e-tax.nta.go.jp/shiyo/index.html ）。
   仕様書本体はリポジトリに入れず、`docs/etax_spec/README.md` に版・取得日・sha256 を記録する。
2. `<年度>/fields.json` を作る:

```json
{
  "spec_version": "令和9年1月4日受付開始分 確定版（2026-11-25）",
  "encoding": "UTF-8",
  "root": "DATA",
  "nsmap": {},
  "xsd": "example.xsd",
  "fields": [
    {"path": "KOA020/ABA00010", "source": "computed.values.business.income", "type": "int", "required": true}
  ]
}
```

   - `source` は版（TaxReturnVersion）の `computed` / `summary` / `inputs` のキー（ドット区切り。計算値のキーにドットが含まれるものは全体で一致させる）。
   - 帳票 ID・要素名・必須は仕様書どおりに。人が仕様書と照合してレビューする。
3. XSD を `settings.TAX_XTX_SCHEMA_DIR/<年度>/` に置く（リポジトリ外）。無ければ `validation.schema = "missing"` となり公開できない。
4. 公式 e-Tax ソフト（ダウンロード版）に組み込んで全帳票の表示・金額一致を確認し、`docs/etax_spec/import_test_R<n>.md` に記録する。
5. 合格した年度だけ環境変数 `TAX_XTX_RELEASED_YEARS=2026` に加える。これで利用者がダウンロードできる（それまでは運営者のみ）。

注意: 確定申告書等作成コーナーは他社ソフトの .xtx を取り込めない（公式 FAQ）。XTX は e-Tax ソフト（WEB 版／ダウンロード版）で読み込む利用者向け。
