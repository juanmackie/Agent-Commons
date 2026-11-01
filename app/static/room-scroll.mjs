export function isNearBottom(feed, threshold = 60) {
  return feed.scrollHeight - feed.scrollTop - feed.clientHeight <= threshold;
}

export function scrollTopAfterPrepend(previousTop, previousHeight, nextHeight) {
  return previousTop + nextHeight - previousHeight;
}
