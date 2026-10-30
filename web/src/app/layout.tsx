import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Folio · 知识有据可循",
  description: "从文档到答案，每一个回答都有出处。企业知识库问答工作台。",
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="zh-CN">
      <body>{children}</body>
    </html>
  );
}
