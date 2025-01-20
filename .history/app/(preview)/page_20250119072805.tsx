"use client";

import { useState, useRef, useEffect } from "react";
import { Sun, Moon } from "lucide-react";

export default function TerminalChatbot() {
  const [lines, setLines] = useState<string[]>([
    "MakeCloud: What cloud infrastructure are you working with today?",
  ]);
  const [input, setInput] = useState("");
  const [isDarkMode, setIsDarkMode] = useState(true);
  const [resourceType, setResourceType] = useState(""); // Store the resource_type
  const [userResponses, setUserResponses] = useState<string[]>([]); // Store user answers
  const [currentQuestionIndex, setCurrentQuestionIndex] = useState(0); // Track current question
  const terminalRef = useRef<HTMLDivElement>(null);
  const [questions, setQuestions] = useState<string[]>([]);

  useEffect(() => {
    terminalRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [lines]);

  const handleExecute = async () => {
    if (input.trim()) {
      setLines((prevLines) => [...prevLines, `> ${input}`]);

      if (!resourceType) {
        // First input is the resource_type
        setResourceType(input);
        setInput(""); // Clear the input immediately

        try {
          // Fetch questions from the API
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

          // Start asking questions
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
          setInput(""); // Clear input field
        }
      } else {
        // Handle subsequent answers
        setUserResponses((prevResponses) => [...prevResponses, input]);
        setInput(""); // Clear the input immediately

        // Move to the next question
        if (currentQuestionIndex + 1 < questions.length) {
          setCurrentQuestionIndex((prevIndex) => prevIndex + 1);
          setLines((prevLines) => [
            ...prevLines,
            `MakeCloud: ${questions[currentQuestionIndex + 1]}`,
          ]);
        } else {
          // If all questions are answered, concatenate responses and send them back
          const concatenatedAnswers = userResponses
            .concat(input)
            .map((answer, index) => `Q${index + 1}: ${answer}`)
            .join(", ");
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
            );

            if (!sendResponse.ok) {
              throw new Error(`HTTP error! Status: ${sendResponse.status}`);
            }

            const finalResponse = await sendResponse.json();
            setLines((prevLines) => [
              ...prevLines,
              `MakeCloud: ${finalResponse}`, // Display the response message
            ]);
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
      className={`fade-in relative w-full h-screen flex flex-col justify-center items-center transition-colors duration-500 ${
        isDarkMode ? "bg-black text-white" : "bg-white text-black"
      }`}
    >
      {/* Header */}
      <header className="w-full max-w-2xl flex justify-between items-center px-4 py-2 border-b">
        <div className="flex items-center space-x-2">
          <span className="text-lg font-bold">☁️ MakeCloud</span>
          <span className="text-sm">Your AI-powered Cloud Assistant</span>
        </div>
        <button
          onClick={toggleTheme}
          className="p-2 rounded-full border focus:outline-none"
        >
          {isDarkMode ? (
            <Sun className="h-5 w-5 text-yellow-400" />
          ) : (
            <Moon className="h-5 w-5 text-indigo-600" />
          )}
        </button>
      </header>

      {/* Terminal Display */}
      <div
        className={`w-full max-w-2xl flex-grow p-6 overflow-y-auto border rounded-lg ${
          isDarkMode
            ? "bg-black text-gray-200 border-gray-700"
            : "bg-white text-gray-800 border-gray-300"
        }`}
        style={{
          fontFamily: "IBM Plex Mono, monospace",
          whiteSpace: "pre-wrap",
        }}
      >
        {lines.map((line, index) => (
          <div key={index}>{line}</div>
        ))}
        <div ref={terminalRef} />
      </div>

      {/* Input Field */}
      <div
        className={`w-full max-w-2xl flex items-center px-4 py-2 border-t ${
          isDarkMode
            ? "bg-black text-gray-200 border-gray-700"
            : "bg-white text-gray-800 border-gray-300"
        }`}
      >
        <span className="text-gray-500 mr-2">{`>`}</span>
        <input
          type="text"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && handleExecute()}
          className={`flex-grow px-4 py-2 rounded-lg focus:outline-none ${
            isDarkMode
              ? "bg-black text-gray-200 border-gray-700"
              : "bg-white text-gray-800 border-gray-300"
          }`}
          placeholder="Type your command..."
          style={{ fontFamily: "IBM Plex Mono, monospace" }}
        />
      </div>
    </div>
  );
}