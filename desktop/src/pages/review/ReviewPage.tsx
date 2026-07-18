export function ReviewPage() {
  return (
    <div class="page page-review">
      <div class="review-toolbar">
        <input class="review-jump-input" type="number" placeholder="跳转到 #" />
        <button class="btn btn--sm">跳转</button>
      </div>
      <div class="review-list">
        <p class="review-placeholder">选择一个文件开始校对</p>
      </div>
    </div>
  );
}
