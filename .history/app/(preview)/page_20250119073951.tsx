"use client";

import { useState, useRef, useEffect } from "react";
import {
  SunIcon,
  MoonIcon,
  DocumentIcon,
  ComputerDesktopIcon,
  Cog6ToothIcon,
} from "@heroicons/react/24/outline";

export default function TerminalChatbot() {
  const [lines, setLines] = useState<string[]>([
    "MakeCloud: What cloud infrastructure are you working with today?",
  ]);
  const [input, setInput] = useState("");
  const [isDarkMode, setIsDarkMode] = useState(true);
  const [resourceType, setResourceType] = useState("");
  const [userResponses, setUserResponses] = useState<string[]>([]);
  const [currentQuestionIndex, setCurrentQuestionIndex] = useState(0);
  const terminalRef = useRef<HTMLDivElement>(null);
  const [questions, setQuestions] = useState<string[]>([]);

  useEffect(() => {
    terminalRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [lines]);

  const handleExecute = async () => {
    if (input.trim()) {
      setLines((prevLines) => [...prevLines, `> ${input}`]);

      if (!resourceType) {
        setResourceType(input);
        setInput("");

        try {
          const response = await fetch(
            `https://cloudy-bitter-darkness-3933.fly.dev/get_info?resource_type=${encodeURIComponent(
              input
            )}`,
            {
              method: "GET",
              headers: {
                Accept: "application/json",
              },
            }
          );

          if (!response.ok) {
            throw new Error(`HTTP error! Status: ${response.status}`);
          }

          const questionsFromAPI = await response.json();
          setQuestions(questionsFromAPI);

          if (questionsFromAPI.length > 0) {
            setLines((prevLines) => [
              ...prevLines,
              `MakeCloud: ${questionsFromAPI[0]}`,
            ]);
          }
        } catch (error) {
          setLines((prevLines) => [
            ...prevLines,
            `MakeCloud: Error fetching questions. ${error}`,
          ]);
          console.error("Fetch Error:", error);
        } finally {
          setInput("");
        }
      } else {
        setUserResponses((prevResponses) => [...prevResponses, input]);
        setInput("");

        if (currentQuestionIndex + 1 < questions.length) {
          setCurrentQuestionIndex((prevIndex) => prevIndex + 1);
          setLines((prevLines) => [
            ...prevLines,
            `MakeCloud: ${questions[currentQuestionIndex + 1]}`,
          ]);
        } else {
          setLines((prevLines) => [
            ...prevLines,
            `MakeCloud: Sending responses back to AI...`,
          ]);

          try {
            const sendResponse = await fetch(
              "http://localhost:5000/generate_script",  // Note: http, not https
              {
                method: "POST",
                body: JSON.stringify({
                  resource_type: resourceType,
                  questions: questions,
                  answers: userResponses.concat(input),
                }),
                headers: {
                  "Content-Type": "application/json",
                  Accept: "application/json",
                },
              }
            );

            if (!sendResponse.ok) {
              throw new Error(`HTTP error! Status: ${sendResponse.status}`);
            }

            setLines((prevLines) => [...prevLines, `MakeCloud: Successful!`]);
          } catch (error) {
            setLines((prevLines) => [
              ...prevLines,
              `MakeCloud: Error sending answers. ${error}`,
            ]);
            console.error("Fetch Error:", error);
          }
        }
      }
    }
  };

  const toggleTheme = () => setIsDarkMode((prev) => !prev);

  return (
    <div
      className={`relative w-full h-screen flex justify-center items-center transition-colors duration-500 ${
        isDarkMode ? "bg-black text-white" : "bg-white text-black"
      }`}
      style={{
        backgroundImage: `url('https://www.transparenttextures.com/patterns/cartographer.png')`,
        backgroundRepeat: "repeat",
        backgroundSize: "auto",
        animation: "moveBackground 30s linear infinite",
      }}
    >
      <style jsx>{`
        @keyframes moveBackground {
          0% {
            background-position: 0 0;
          }
          100% {
            background-position: 100px 100px;
          }
        }
      `}</style>

      {/* Terminal Box */}
      <div className="flex rounded-lg overflow-hidden shadow-lg max-w-4xl bg-opacity-100 border border-gray-600">
        {/* Sidebar */}
        <aside
          className={`w-16 flex flex-col items-center space-y-4 py-4 ${
            isDarkMode ? "bg-gray-800" : "bg-blue-100"
          }`}
        >
          <button className="p-2 rounded hover:bg-gray-700 focus:outline-none">
            <DocumentIcon className="h-6 w-6 text-blue-400" />
          </button>
          <button className="p-2 rounded hover:bg-gray-700 focus:outline-none">
            <ComputerDesktopIcon className="h-6 w-6 text-green-400" />
          </button>
          <button className="p-2 rounded hover:bg-gray-700 focus:outline-none">
            <Cog6ToothIcon className="h-6 w-6 text-red-400" />
          </button>
        </aside>

        {/* Terminal Content */}
        <div
          className={`flex flex-col flex-grow p-4 ${
            isDarkMode ? "bg-gray-900 text-gray-200" : "bg-gray-100 text-gray-800"
          }`}
          style={{
            fontFamily: "'IBM Plex Mono', monospace",
            whiteSpace: "pre-wrap",
          }}
        >
          {/* Header */}
          <header className="flex justify-between items-center mb-4">
            <div>
              <h1 className="text-lg font-bold">☁️ MakeCloud</h1>
              <p className="text-sm">Your AI-powered Cloud Assistant</p>
            </div>
            <button
              onClick={toggleTheme}
              className="p-2 rounded-full border focus:outline-none"
            >
              {isDarkMode ? (
                <SunIcon className="h-5 w-5 text-yellow-400" />
              ) : (
                <MoonIcon className="h-5 w-5 text-indigo-600" />
              )}
            </button>
          </header>

          {/* Messages */}
          <div className="flex-grow overflow-y-auto mb-4">
            {lines.map((line, index) => (
              <div
                key={index}
                className={isDarkMode ? "text-gray-300" : "text-gray-600"}
                style={{ fontFamily: "'IBM Plex Mono', monospace" }}
              >
                {line}
              </div>
            ))}
            <div ref={terminalRef} />
          </div>

          {/* Input Field */}
          <div className="flex items-center space-x-2">
            <span
              className={`text-gray-500 mr-2 ${isDarkMode ? "text-gray-400" : "text-gray-700"}`}
              style={{ fontFamily: "'IBM Plex Mono', monospace" }}
            >
              {`>`}
            </span>
            <input
              type="text"
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && handleExecute()}
              className={`flex-grow p-2 rounded-lg focus:outline-none ${
                isDarkMode
                  ? "bg-gray-800 text-gray-200 border-gray-700"
                  : "bg-white text-gray-800 border-gray-300"
              }`}
              placeholder="Type your command..."
              style={{ fontFamily: "'IBM Plex Mono', monospace" }}
            />
            <button
              onClick={() => handleExecute()}
              className={`px-4 py-2 rounded font-bold ${
                isDarkMode
                  ? "bg-indigo-500 hover:bg-indigo-600 text-white"
                  : "bg-blue-500 hover:bg-blue-600 text-white"
              }`}
            >
              Run
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}