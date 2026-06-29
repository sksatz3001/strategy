"use client";

import { createChart, ISeriesApi, UTCTimestamp } from "lightweight-charts";
import { useEffect, useRef } from "react";

interface Point {
  ts: string;
  equity: number;
}

export function EquityChart({ data }: { data: Point[] }) {
  const hostRef = useRef<HTMLDivElement | null>(null);
  const seriesRef = useRef<ISeriesApi<"Line"> | null>(null);

  useEffect(() => {
    if (!hostRef.current) {
      return;
    }

    const chart = createChart(hostRef.current, {
      layout: {
        background: { color: "#09131f" },
        textColor: "#c9d8ea",
      },
      grid: {
        vertLines: { color: "rgba(128, 155, 187, 0.12)" },
        horzLines: { color: "rgba(128, 155, 187, 0.12)" },
      },
      rightPriceScale: {
        borderVisible: false,
      },
      timeScale: {
        borderVisible: false,
      },
      width: hostRef.current.clientWidth,
      height: 280,
    });

    const series = chart.addLineSeries({
      color: "#34d399",
      lineWidth: 3,
      priceLineVisible: false,
    });
    seriesRef.current = series;

    const resize = () => {
      if (hostRef.current) {
        chart.applyOptions({ width: hostRef.current.clientWidth });
      }
    };

    window.addEventListener("resize", resize);
    return () => {
      window.removeEventListener("resize", resize);
      chart.remove();
      seriesRef.current = null;
    };
  }, []);

  useEffect(() => {
    if (!seriesRef.current) {
      return;
    }

    seriesRef.current.setData(
      data.map((point) => ({
        time: Math.floor(new Date(point.ts).getTime() / 1000) as UTCTimestamp,
        value: point.equity,
      }))
    );
  }, [data]);

  return <div className="chart-host" ref={hostRef} />;
}
