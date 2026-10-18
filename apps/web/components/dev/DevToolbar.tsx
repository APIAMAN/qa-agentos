'use client';

import dynamic from 'next/dynamic';

const TwentyFirstToolbar = dynamic(
  () =>
    import('@21st-extension/toolbar-next').then((mod) => mod.TwentyFirstToolbar),
  { ssr: false }
);

export function DevToolbar() {
  if (process.env.NODE_ENV !== 'development') {
    return null;
  }

  return <TwentyFirstToolbar config={{ plugins: [] }} />;
}
