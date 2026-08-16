import "./globals.css";

export const metadata = {
  title: "Supplier Risk — Multi-Source Retrieval",
  description: "SQL + FAISS + Neo4j fired in parallel, synthesized by a compiled DSPy module",
};

export default function RootLayout({ children }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
