import type { Metadata, Viewport } from "next";
import "./globals.css";
import { SessionProvider } from "@/lib/session";

export const metadata: Metadata = {
  title: "NOXIA - ラウンジ店舗運営",
  description: "小規模ラウンジ向けの注文・会計・延長管理・バック集計・日締め・店舗管理",
  manifest: "/manifest.webmanifest",
  appleWebApp: { capable: true, statusBarStyle: "black-translucent", title: "NOXIA" },
  // ブラウザのタブ・PC へのインストール・iPad の「ホーム画面に追加」で NOXIA のロゴを使う
  icons: {
    icon: [
      { url: "/favicon.ico", sizes: "any" },
      { url: "/icon-192.png", type: "image/png", sizes: "192x192" },
      { url: "/icon-512.png", type: "image/png", sizes: "512x512" },
    ],
    apple: [{ url: "/apple-touch-icon.png", sizes: "180x180" }],
  },
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  maximumScale: 1,
  viewportFit: "cover",
  themeColor: "#0f1116",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="ja">
      <body>
        <SessionProvider>{children}</SessionProvider>
      </body>
    </html>
  );
}
