import { Link } from "react-router-dom";

/**
 * 覚えのない URL に来たときの行き先。
 *
 * これが無いと、綴りの違い・古いブックマーク・削除した画面へのリンクで
 * 真っ白な画面になる。読み込みに失敗したのか、権限が無いのか、そもそも
 * そんな画面が無いのかが、見ている側から区別できない。
 */
export default function NotFoundPage() {
  return (
    <div className="page">
      <h1>その画面はありません</h1>
      <p>
        URL の綴りが違うか、古いブックマークかもしれません。開こうとしたのは{" "}
        <code>{window.location.pathname}</code> です。
      </p>
      <p>
        <Link to="/dashboard">概況へ戻る</Link>
      </p>
    </div>
  );
}
